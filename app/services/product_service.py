"""Product Service - Real product management with database queries."""
from typing import Dict, Any, Optional, List
import logging
import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import joinedload

from app.models.product import Product, ProductImage
from app.models.category import Category
from app.models.brand import Brand
from app.schemas.product import ProductCreate, ProductUpdate, ProductFilter, ProductResponse
from app.crud import product as product_crud

logger = logging.getLogger(__name__)


class ProductService:
    """Service for managing building material products with real DB queries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_products(
        self,
        filters: ProductFilter,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """Get products with filtering, search, and pagination using real DB queries."""
        query = select(
            Product, Category.name, Category.division, Category.material_type, Brand.name
        ).join(
            Category, Product.category_id == Category.id
        ).outerjoin(
            Brand, Product.brand_id == Brand.id
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
            query = query.where(
                or_(
                    Product.name.ilike(search_term),
                    Product.description.ilike(search_term),
                    Product.sku.ilike(search_term),
                )
            )
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
            query = query.where(Category.name.ilike(filters.category))

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
            products.append(product_dict)

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
        return {
            **product.__dict__,
            "category": category_name,
            "category_division": category_division,
            "category_material_type": category_material_type,
            "brand_name": brand_name,
        }

    async def create_product(self, product_in: ProductCreate, vendor_id: UUID) -> Product:
        """Create a new product listing in the database."""
        product_data = product_in.dict(exclude_unset=True)
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

        update_data = product_in.dict(exclude_unset=True)
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
