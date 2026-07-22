from typing import Optional, List
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.crud.base import CRUDBase
from app.models.product import Product
from app.models.category import Category
from app.schemas.product import ProductCreate, ProductUpdate


class CRUDProduct(CRUDBase[Product, ProductCreate, ProductUpdate]):
    ### Get product by slug
    async def get_by_slug(self, db: AsyncSession, *, slug: str) -> Optional[Product]:
        result = await db.execute(select(Product).where(Product.slug == slug))
        return result.scalar_one_or_none()

    ### Get product by SKU
    async def get_by_sku(self, db: AsyncSession, *, sku: str) -> Optional[Product]:
        result = await db.execute(select(Product).where(Product.sku == sku))
        return result.scalar_one_or_none()

    ### Get products by vendor
    async def get_by_vendor(
        self, 
        db: AsyncSession, 
        *, 
        vendor_id: UUID,
        skip: int = 0,
        limit: int = 100
    ) -> List[Product]:
        result = await db.execute(
            select(Product)
            .where(Product.vendor_id == vendor_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    ### Get products by category
    async def get_by_category(
        self, 
        db: AsyncSession, 
        *, 
        category_id: UUID,
        skip: int = 0,
        limit: int = 100
    ) -> List[Product]:
        result = await db.execute(
            select(Product)
            .where(Product.category_id == category_id)
            .where(Product.status == "active")
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()
    
    ### Get category ID by name
    async def get_id_by_name(
        self,
        db: AsyncSession,
        *,
        name: str
    ) -> Optional[UUID]:

        result = await db.execute(
            select(Category.id)
            .where(Category.name == name)
            .where(Category.parent_id.is_(None))
        )

        return result.scalar_one_or_none()
        

    ### Increment product view count
    async def increment_views(self, db: AsyncSession, *, product_id: UUID) -> None:
        product = await self.get(db, id=product_id)
        if product:
            product.view_count += 1
            db.add(product)
            await db.commit()

product = CRUDProduct(Product)
