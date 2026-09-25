from typing import Dict, Any, Optional, List
import logging
import re
import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import joinedload

from app.models.product import Product, ProductImage
from app.models.category import Category
from app.models.brand import Brand
from app.models.vendor import Vendor
from app.schemas.product import ProductCreate, ProductUpdate, ProductFilter, ProductResponse
from app.crud import product as product_crud

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
    let the generic word "systems" — shared by five of the twenty parent
    categories — bleed across them, so selecting "Roofing Systems" also returned
    the "Plumbing Systems" rows (PVC pipes, water tanks).

    Tokens match on word boundaries (`_norm_like`), not as bare substrings:
    "cement" is a substring of "reinfor-cement Steel", so a ``'%cement%'`` LIKE
    also listed reinforcement products under the Cement category.

    RELAXED (chat tool only, on an empty strict result): any token may match, so
    a free-text phrase such as "roofing sheets" still resolves to "Roofing
    Systems". Never use this for a marketplace filter — it is what caused the
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
    ) -> Dict[str, Any]:
        """Get products with filtering, search, and pagination using real DB queries.

        When `only_verified=True`, only products belonging to VERIFIED vendors are
        returned — this is used by the public marketplace so that a pending vendor's
        products stay private (visible only to the vendor themselves via my-products).

        `relaxed_category` is opt-in and used ONLY by the chat tool, as a retry after
        a strict category filter returned nothing. Marketplace requests never set it:
        a relaxed (token-OR) category filter is what made "Roofing Systems" list
        plumbing products.
        """
        query = select(
            Product, Category.name, Category.division, Category.material_type, Brand.name
        ).join(
            Category, Product.category_id == Category.id
        ).outerjoin(
            Brand, Product.brand_id == Brand.id
        )

        # Marketplace gate: only surface products from verified vendors.
        # Marketplace gate: show products from ACTIVE vendors (all tiers, incl 0);
        # only suspended/deactivated/rejected are hidden. Tiers gate selling, not visibility.
        if only_verified:
            query = query.join(Vendor, Vendor.id == Product.vendor_id).where(
                Vendor.verification_status.notin_(["suspended", "deactivated", "rejected"])
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
            product, category_name, category_division, category_material_type, brand_name = row
            product_dict = {
                **product.__dict__,
                "category": category_name,
                "category_division": category_division,
                "category_material_type": category_material_type,
                "brand_name": brand_name,
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
        """Get a single product by ID with category info."""
        result = await self.db.execute(
            select(Product, Category.name, Category.division, Category.material_type, Brand.name)
            .join(Category, Product.category_id == Category.id)
            .outerjoin(Brand, Product.brand_id == Brand.id)
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

    async def create_product(self, product_in: ProductCreate, vendor_id: UUID) -> Product:
        """Create a new product listing in the database."""
        product_data = product_in.model_dump(exclude_unset=True)
        product_data["vendor_id"] = vendor_id
        product_data["slug"] = self._generate_slug(product_data["name"])

        # Resolve brand_name to brand_id if provided
        brand_name = product_data.pop("brand_name", None)
        if brand_name and not product_data.get("brand_id"):
            result = await self.db.execute(
                select(Brand).where(Brand.name.ilike(brand_name))
            )
            brand = result.scalar_one_or_none()
            if brand:
                product_data["brand_id"] = brand.id
            else:
                logger.warning(f"Brand '{brand_name}' not found, creating new brand")
                brand = Brand(
                    name=brand_name,
                    slug=brand_name.lower().replace(" ", "-"),
                )
                self.db.add(brand)
                await self.db.flush()
                product_data["brand_id"] = brand.id

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
        await self.db.commit()
        await self.db.refresh(product)
        return product

    async def update_product(self, product_id: UUID, product_in: ProductUpdate) -> Optional[Product]:
        """Update an existing product."""
        product = await product_crud.get(self.db, id=product_id)
        if not product:
            return None

        update_data = product_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(product, field, value)

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

    async def upload_product_images(
        self, product_id: UUID, files: List
    ) -> List[Dict[str, Any]]:
        """Upload images for a product."""
        images = []
        for i, file in enumerate(files):
            # In production, upload to cloud storage and get URL
            image = ProductImage(
                product_id=product_id,
                image_url=f"/uploads/{file.filename}",
                is_primary=(i == 0),
                display_order=i,
            )
            self.db.add(image)
            images.append(image)

        await self.db.commit()
        return [
            {"id": str(img.id), "url": img.image_url, "is_primary": img.is_primary}
            for img in images
        ]

    def _generate_slug(self, name: str) -> str:
        """Generate a URL-friendly slug from a name."""
        import re
        slug = name.lower().strip()
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        return f"{slug}-{uuid.uuid4().hex[:8]}"
