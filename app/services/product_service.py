from typing import Dict, Any, Optional, List
import logging
import re
import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_, delete
from sqlalchemy.orm import joinedload, selectinload

from app.models.product import Product, ProductImage, ProductSpecification, ProductStatus
from app.models.category import Category
from app.models.brand import Brand
from app.models.vendor import Vendor
from app.schemas.product import ProductCreate, ProductUpdate, ProductFilter, ProductResponse
from app.crud import product as product_crud
from app.config import settings
from app.utils.image_processing import normalize_image, ImageTooLarge, UnsupportedImage
from app.utils.storage import StorageService

logger = logging.getLogger(__name__)


# ── Search normalisation ─────────────────────────────────────────────────────
# The catalog uses precise names ("Stone-Coated Roofing Sheet") while users and
# the AI ask in natural language ("stone coated roofing sheets"). A raw substring
# ILIKE misses those, so searches are tokenised and each token is matched with
# '-'/' ' treated as equivalent and optional trailing plurals.

_SEARCH_SPLIT = re.compile(r"[\s,/\-&]+")


def _norm_like(column, value: str):
    """Case-insensitive whole-word match, treating '-' and ' ' as equivalent.

    A plain ``LIKE '%cement%'`` also matches "12mm Reinforcement Rod" because
    "reinfor-cement" contains "cement", so word boundaries are enforced with the
    Postgres regex operator.
    """
    pattern = r"\y" + re.escape(value) + r"\y"
    return func.replace(column, "-", " ").op("~*")(pattern)


def _token_variants(token: str) -> set:
    """Plural/singular variants of a search token."""
    variants = {token}
    if token.endswith("s") and len(token) > 3:
        variants.add(token[:-1])
    return variants


def _search_conditions(search: str):
    """AND over a query's tokens; each token tolerates plural/hyphen variants.

    Returns None when the query has no usable token (all shorter than 3 chars).
    """
    groups = []
    for raw in _SEARCH_SPLIT.split((search or "").strip().lower()):
        if len(raw) < 3:
            continue
        conds = []
        for variant in _token_variants(raw):
            conds.append(_norm_like(Product.name, variant))
            conds.append(_norm_like(Product.description, variant))
            conds.append(_norm_like(Product.sku, variant))
            # Seeded rows keep short brand/material/spec words here
            # ("cement, dangote, 50kg, abuja"), which the name alone often misses.
            conds.append(_norm_like(Product.meta_keywords, variant))
        groups.append(or_(*conds))
    return and_(*groups) if groups else None


def _category_conditions(category: str, relaxed: bool = False):
    """Match a category across its name/division/material_type.

    STRICT (the default, used by every marketplace request): every token must
    match in at least one of those fields. Matching the tokens with OR instead
    let the generic word "systems" - shared by five of the twenty parent
    categories - bleed across them, so selecting "Roofing Systems" also returned
    the "Plumbing Systems" rows (PVC pipes, water tanks).

    Tokens match on word boundaries (`_norm_like`), not as bare substrings:
    "cement" is a substring of "reinfor-cement Steel", so a ``'%cement%'`` LIKE
    also listed reinforcement products under the Cement category.

    RELAXED (chat tool only, on an empty strict result): any token may match, so
    a free-text phrase such as "roofing sheets" still resolves to "Roofing
    Systems". Never use this for a marketplace filter - it is what caused the
    wrong results.
    """
    groups = []
    for raw in _SEARCH_SPLIT.split((category or "").strip().lower()):
        if len(raw) < 3:
            continue
        groups.append(or_(
            # Word-boundary matching, not a bare '%token%' LIKE: "cement" is a
            # substring of "reinfor-cement Steel", so substring matching listed
            # reinforcement products under the Cement pill. `_norm_like` also
            # treats '-' and ' ' as equivalent, so category names tokenise like
            # the marketplace's slugs.
            _norm_like(Category.name, raw),
            _norm_like(Category.division, raw),
            _norm_like(Category.material_type, raw),
        ))
    if not groups:
        return None
    return or_(*groups) if relaxed else and_(*groups)


class ProductService:
    """Service for managing building material products with real DB queries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _coerce_product_dict(product_dict: dict) -> dict:
        """Fill None scalar fields with documented defaults so the API
        response schema (non-nullable int/bool/Decimal) never fails.

        ORM Python-side defaults only apply when rows are inserted through
        SQLAlchemy; rows seeded via raw SQL can leave these columns NULL.
        """
        for f in ("minimum_order_quantity", "view_count", "sales_count", "quantity", "review_count"):
            if product_dict.get(f) is None:
                product_dict[f] = 1 if f == "minimum_order_quantity" else 0
        if product_dict.get("rating") is None:
            product_dict["rating"] = 0
        for f in ("is_featured", "is_verified"):
            if product_dict.get(f) is None:
                product_dict[f] = False
        return product_dict

    async def list_products(
        self,
        filters: ProductFilter,
        page: int = 1,
        page_size: int = 20,
        only_verified: bool = False,
        relaxed_category: bool = False,
        marketplace_visible: bool = False,
    ) -> Dict[str, Any]:
        """Get products with filtering, search, and pagination using real DB queries.

        When `only_verified=True`, only products belonging to VERIFIED vendors are
        returned - this is used by the public marketplace so that a pending vendor's
        products stay private (visible only to the vendor themselves via my-products).

        `marketplace_visible=True` additionally hides products that must not appear in
        the public marketplace: drafts/pending, vendor-hidden (is_featured is the
        visibility flag used by the supplier forms), and out-of-stock listings. It is
        scoped to the public listing endpoint so pricing/checkout callers that reuse
        `only_verified` keep their existing catalogue.

        `relaxed_category` is opt-in and used ONLY by the chat tool, as a retry after
        a strict category filter returned nothing. Marketplace requests never set it:
        a relaxed (token-OR) category filter is what made "Roofing Systems" list
        plumbing products.
        """
        # The supplier line under a product title needs the vendor's business name
        # and logo URL. Correlated scalar subqueries (not a joined Vendor) so these
        # stay valid alongside the optional verified-vendor join below.
        vendor_name_subq = (
            select(Vendor.business_name)
            .where(Vendor.id == Product.vendor_id)
            .limit(1)
            # Correlate on Product only: without this, SQLAlchemy auto-correlates the
            # subquery against the outer Vendor join (only_verified) and compiles a
            # subquery with no FROM clause.
            .correlate(Product)
            .scalar_subquery()
            .label("vendor_name")
        )
        vendor_image_subq = (
            select(Vendor.business_image)
            .where(Vendor.id == Product.vendor_id)
            .limit(1)
            .correlate(Product)
            .scalar_subquery()
            .label("vendor_image")
        )

        query = select(
            Product, Category.name, Category.division, Category.material_type, Brand.name,
            vendor_name_subq, vendor_image_subq,
        ).join(
            Category, Product.category_id == Category.id
        ).outerjoin(
            Brand, Product.brand_id == Brand.id
        ).options(
            selectinload(Product.images),
            selectinload(Product.specifications),
        )

        # Marketplace gate: only surface products from verified vendors.
        # Marketplace gate: show products from ACTIVE vendors (all tiers, incl 0);
        # only suspended/deactivated/rejected are hidden. Tiers gate selling, not visibility.
        if only_verified:
            query = query.join(Vendor, Vendor.id == Product.vendor_id).where(
                Vendor.verification_status.notin_(["suspended", "deactivated", "rejected"])
            )

        # Public marketplace visibility gate: drafts/pending products, products the
        # vendor hid (is_featured is the product visibility flag on the supplier
        # forms), and out-of-stock products must never be listed.
        if marketplace_visible:
            query = query.where(
                Product.status == ProductStatus.ACTIVE,
                Product.is_featured == True,  # noqa: E712 - SQL expression, not a bool
                Product.quantity > 0,
            )

        # Apply filters
        if filters.category_id:
            query = query.where(Product.category_id == filters.category_id)
        if filters.vendor_id:
            query = query.where(Product.vendor_id == filters.vendor_id)
        if filters.brand_id:
            query = query.where(Product.brand_id == filters.brand_id)
        if filters.search:
            search_term = f"%{filters.search}%"
            # Searching a supplier name as a subquery (instead of joining Vendor)
            # keeps this valid whether or not the verified-vendor join is present.
            vendor_match = select(Vendor.id).where(Vendor.business_name.ilike(search_term))
            # Brand rows ("Dangote Cement") often hold the brand the product name
            # shortens ("Dangote Cement 50kg"), so match them too.
            brand_match = select(Brand.id).where(Brand.name.ilike(search_term))
            token_cond = _search_conditions(filters.search)
            if token_cond is not None:
                # Natural-language queries ("stone-coated roofing sheets") match on
                # every token instead of the literal phrase.
                query = query.where(or_(
                    token_cond,
                    Product.vendor_id.in_(vendor_match),
                    Product.brand_id.in_(brand_match),
                ))
            else:
                query = query.where(
                    or_(
                        Product.name.ilike(search_term),
                        Product.description.ilike(search_term),
                        Product.sku.ilike(search_term),
                        Product.vendor_id.in_(vendor_match),
                        Product.brand_id.in_(brand_match),
                    )
                )
        if filters.in_stock:
            query = query.where(Product.quantity > 0)
        if filters.min_price is not None:
            query = query.where(Product.base_price >= filters.min_price)
        if filters.max_price is not None:
            query = query.where(Product.base_price <= filters.max_price)
        if filters.is_featured is not None:
            query = query.where(Product.is_featured == filters.is_featured)
        if filters.division:
            query = query.where(Category.division == filters.division)
        if filters.material_type:
            query = query.where(Category.material_type == filters.material_type)

        if filters.category:
            cat_cond = _category_conditions(
                filters.category, relaxed=relaxed_category
            )
            query = query.where(
                cat_cond if cat_cond is not None else Category.name.ilike(filters.category)
            )

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        # Sort
        sort_map = {
            "created_at": Product.created_at,
            "price": Product.base_price,
            "rating": Product.rating,
            "sales_count": Product.sales_count,
            "name": Product.name,
        }
        sort_column = sort_map.get(filters.sort_by, Product.created_at)
        if filters.sort_order == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

        # Paginate
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        rows = result.all()

        products = []
        for row in rows:
            product, category_name, category_division, category_material_type, brand_name, vendor_name, vendor_image = row
            product_dict = {
                **product.__dict__,
                "category": category_name,
                "category_division": category_division,
                "category_material_type": category_material_type,
                "brand_name": brand_name,
                "vendor_name": vendor_name,
                "vendor_image": vendor_image,
            }
            products.append(self._coerce_product_dict(product_dict))

        return {
            "products": products,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
        }

    async def get_product_by_id(self, product_id: UUID) -> Optional[Dict[str, Any]]:
        """Get a single product by ID with category info, images and specs."""
        result = await self.db.execute(
            select(Product, Category.name, Category.division, Category.material_type, Brand.name)
            .join(Category, Product.category_id == Category.id)
            .outerjoin(Brand, Product.brand_id == Brand.id)
            .options(
                selectinload(Product.images),
                selectinload(Product.specifications),
            )
            .where(Product.id == product_id)
        )
        row = result.first()
        if not row:
            return None

        product, category_name, category_division, category_material_type, brand_name = row
        return self._coerce_product_dict({
            **product.__dict__,
            "category": category_name,
            "category_division": category_division,
            "category_material_type": category_material_type,
            "brand_name": brand_name,
        })

    @staticmethod
    def _brand_slug(name: str) -> str:
        """URL-safe brand slug, matching how catalogue brands are keyed."""
        return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")

    async def resolve_brand_id(self, brand_name: str) -> UUID:
        """Map a brand display name to a brand row, creating one only if unknown.

        Shared by the create and update paths: Product has no brand_name column,
        so an edit that changes the brand has to resolve the name to the FK here.
        """
        result = await self.db.execute(
            select(Brand).where(Brand.name.ilike(brand_name.strip())).limit(1)
        )
        brand = result.scalar_one_or_none()
        if brand:
            return brand.id

        logger.warning(f"Brand '{brand_name}' not found, creating new brand")
        brand = Brand(name=brand_name.strip(), slug=self._brand_slug(brand_name))
        self.db.add(brand)
        await self.db.flush()
        return brand.id

    async def _unique_sku(self, sku: str, max_suffixes: int = 5) -> str:
        """Return `sku`, or `sku-N` when the catalog SKU is already taken.

        Catalog SKUs are shared by every vendor listing the same product, so a
        collision is expected rather than an error: suffix -2..-6 and let the
        create response carry the final SKU back to the form. Exceeding the
        suffixes raises ``ValueError`` (mapped to HTTP 400 by the endpoint).
        """
        candidate = sku
        for suffix in range(2, max_suffixes + 2):
            clash = await product_crud.get_by_sku(self.db, sku=candidate)
            if not clash:
                return candidate
            candidate = f"{sku}-{suffix}"
        raise ValueError(
            f"SKU '{sku}' is unavailable. Please choose a different SKU."
        )

    async def create_product(self, product_in: ProductCreate, vendor_id: UUID) -> Product:
        """Create a new product listing in the database."""
        product_data = product_in.model_dump(exclude_unset=True)
        product_data["vendor_id"] = vendor_id
        product_data["slug"] = self._generate_slug(product_data["name"])

        # Pull specifications out before constructing the ORM row - they live in
        # a child table, not on Product itself.
        specifications = product_data.pop("specifications", None) or []

        # Same for image_url: Product has no such column, so it must be popped
        # before Product(**product_data) or the create raises -> HTTP 500.
        image_url = product_data.pop("image_url", None)

        # Listings share catalog SKUs across vendors: suffix a collision instead
        # of failing the create (the endpoint returns the final SKU).
        if product_data.get("sku"):
            product_data["sku"] = await self._unique_sku(product_data["sku"])

        # Resolve brand_name to brand_id if provided: the supplier forms send a
        # brand_id whenever the catalogue holds the brand, so this only covers a
        # brand that is genuinely new (or a client that still sends a name).
        brand_name = product_data.pop("brand_name", None)
        if brand_name and not product_data.get("brand_id"):
            product_data["brand_id"] = await self.resolve_brand_id(brand_name)

        # Resolve category_id if a category name string was passed
        category_id = product_data.get("category_id")
        if category_id and isinstance(category_id, str):
            try:
                product_data["category_id"] = UUID(category_id)
            except ValueError:
                # It might be a category name, look it up
                result = await self.db.execute(
                    select(Category).where(Category.name.ilike(category_id))
                )
                category = result.scalar_one_or_none()
                if category:
                    product_data["category_id"] = category.id
                else:
                    raise ValueError(f"Category '{category_id}' not found")

        product = Product(**product_data)
        self.db.add(product)
        await self.db.flush()  # get product.id without committing yet

        # Catalog image = primary image (slide 1 of the gallery). Vendor uploads
        # always land after it as non-primary slides.
        if image_url and str(image_url).strip():
            self.db.add(ProductImage(
                product_id=product.id,
                image_url=str(image_url).strip(),
                alt_text=(product.name or "Product image")[:255],
                display_order=0,
                is_primary=True,
            ))

        # Upsert specifications in display_order order.
        for idx, spec in enumerate(specifications):
            if not spec.spec_value.strip():
                continue  # skip blank spec rows
            self.db.add(ProductSpecification(
                product_id=product.id,
                spec_name=spec.spec_name.strip(),
                spec_value=spec.spec_value.strip(),
                display_order=spec.display_order if spec.display_order is not None else idx,
            ))

        await self.db.commit()
        await self.db.refresh(product)
        return product

    async def update_product(self, product_id: UUID, product_in: ProductUpdate) -> Optional[Product]:
        """Update an existing product.

        Mirrors ``PUT /products/{id}``: scalars are applied as-is, ``brand_name``
        resolves to ``brand_id``, ``specifications`` replace their child rows
        (never assigned onto the relationship, which would raise on flush), and a
        colliding ``sku`` raises ``ValueError`` rather than an IntegrityError.
        """
        product = await product_crud.get(self.db, id=product_id)
        if not product:
            return None

        update_data = product_in.model_dump(exclude_unset=True)
        specs_provided = "specifications" in update_data
        update_data.pop("specifications", None)

        # image_url is write-only (Product has no such column): it replaces the
        # product's primary image row below.
        image_url = update_data.pop("image_url", None)

        # Product has no brand_name column: resolve the write-only name to the FK.
        brand_name = update_data.pop("brand_name", None)
        if brand_name and not update_data.get("brand_id"):
            update_data["brand_id"] = await self.resolve_brand_id(brand_name)

        # sku is unique across products.
        new_sku = update_data.get("sku")
        if new_sku and new_sku != product.sku:
            clash = await product_crud.get_by_sku(self.db, sku=new_sku)
            if clash and clash.id != product.id:
                raise ValueError(
                    f"A product with SKU '{new_sku}' already exists. Please use a different SKU."
                )

        for field, value in update_data.items():
            setattr(product, field, value)

        # Replace the primary image when the client sent one (empty string just
        # clears it). Vendor-uploaded slides are non-primary and left untouched.
        if image_url is not None:
            await self.db.execute(
                delete(ProductImage).where(
                    ProductImage.product_id == product.id,
                    ProductImage.is_primary == True,  # noqa: E712 - SQLAlchemy column
                )
            )
            if str(image_url).strip():
                self.db.add(ProductImage(
                    product_id=product.id,
                    image_url=str(image_url).strip(),
                    alt_text=(product.name or "Product image")[:255],
                    display_order=0,
                    is_primary=True,
                ))

        # Replace child rows; an empty array clears them, omitting leaves them.
        if specs_provided:
            await self.db.execute(
                delete(ProductSpecification).where(
                    ProductSpecification.product_id == product.id
                )
            )
            for idx, spec in enumerate(product_in.specifications or []):
                if not (spec.spec_value or "").strip():
                    continue
                self.db.add(ProductSpecification(
                    product_id=product.id,
                    spec_name=spec.spec_name.strip(),
                    spec_value=spec.spec_value.strip(),
                    display_order=spec.display_order if spec.display_order is not None else idx,
                ))

        await self.db.commit()
        await self.db.refresh(product)
        return product

    async def delete_product(self, product_id: UUID) -> bool:
        """Delete a product."""
        product = await product_crud.get(self.db, id=product_id)
        if not product:
            return False
        await self.db.delete(product)
        await self.db.commit()
        return True

    async def increment_view_count(self, product_id: UUID) -> None:
        """Increment the view count for a product."""
        product = await product_crud.get(self.db, id=product_id)
        if product:
            product.view_count = (product.view_count or 0) + 1
            await self.db.commit()

    async def _existing_image_count(self, product_id: UUID) -> int:
        result = await self.db.execute(
            select(func.count(ProductImage.id)).where(ProductImage.product_id == product_id)
        )
        return result.scalar() or 0

    async def upload_product_images(
        self, product_id: UUID, files: List
    ) -> List[Dict[str, Any]]:
        """Upload (normalise + store) vendor photos for a product.

        Enforces ``PRODUCT_IMAGE_MAX_COUNT`` vendor slides per product
        (the platform category image is slide 1, stored separately as the
        category default). Every photo is normalised through
        :func:`normalize_image` with the product byte budget so no stored
        image exceeds ``PRODUCT_IMAGE_MAX_BYTES``.
        """
        max_count = settings.PRODUCT_IMAGE_MAX_COUNT
        existing = await self._existing_image_count(product_id)
        if existing + len(files) > max_count:
            raise ValueError(
                f"At most {max_count} vendor photos per product "
                f"({existing} already uploaded)."
            )

        storage = StorageService()
        folder = f"products/{product_id}"
        uploaded_urls: List[str] = []
        images: List[ProductImage] = []

        try:
            # Determine the next display_order after existing rows.
            max_order_result = await self.db.execute(
                select(func.coalesce(func.max(ProductImage.display_order), -1))
                .where(ProductImage.product_id == product_id)
            )
            next_order = (max_order_result.scalar() or -1) + 1

            for i, file in enumerate(files):
                # Gate: reject oversized uploads before decoding (mirrors
                # drivers.py upload pattern).
                content = await file.read(settings.PRODUCT_IMAGE_MAX_UPLOAD_BYTES + 1)
                if len(content) > settings.PRODUCT_IMAGE_MAX_UPLOAD_BYTES:
                    raise ValueError(
                        f"{file.filename or 'image'}: file is larger than "
                        f"{settings.PRODUCT_IMAGE_MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                    )

                try:
                    normalized = normalize_image(
                        content,
                        max_bytes=settings.PRODUCT_IMAGE_MAX_BYTES,
                        max_edge=settings.PRODUCT_IMAGE_MAX_EDGE,
                        min_edge=settings.PRODUCT_IMAGE_MIN_EDGE,
                        preserve_metadata=True,
                    )
                except UnsupportedImage as exc:
                    raise ValueError(
                        f"{file.filename or 'image'}: {exc}"
                    ) from exc
                except ImageTooLarge as exc:
                    raise ValueError(
                        f"{file.filename or 'image'}: {exc}"
                    ) from exc

                url = await storage.upload_file(
                    file=normalized,
                    folder=folder,
                    filename=f"{uuid.uuid4().hex}.jpg",
                )
                uploaded_urls.append(url)

                image = ProductImage(
                    product_id=product_id,
                    image_url=url,
                    alt_text=file.filename or "Product photo",
                    is_primary=False,  # platform category image is slide 1
                    display_order=next_order + i,
                )
                self.db.add(image)
                images.append(image)

            await self.db.commit()
            for img in images:
                await self.db.refresh(img)

            return [
                {"id": str(img.id), "url": img.image_url, "is_primary": img.is_primary}
                for img in images
            ]

        except Exception:
            # Never leave orphaned storage objects or a half-applied row behind
            # when a later file in the batch fails validation.
            await self.db.rollback()
            for url in uploaded_urls:
                try:
                    await storage.delete_file(url)
                except Exception:  # best-effort cleanup only
                    pass
            raise

    def _generate_slug(self, name: str) -> str:
        """Generate a URL-friendly slug from a name."""
        import re
        slug = name.lower().strip()
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        return f"{slug}-{uuid.uuid4().hex[:8]}"
