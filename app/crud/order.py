from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.crud.base import CRUDBase
from app.models.order import Order
from app.schemas.order import OrderCreate, OrderUpdate


### Get order by order number
class CRUDOrder(CRUDBase[Order, OrderCreate, OrderUpdate]):
    async def get_by_order_number(
        self, 
        db: AsyncSession, 
        *, 
        order_number: str
    ) -> Optional[Order]:
        result = await db.execute(
            select(Order).where(Order.order_number == order_number)
        )
        return result.scalar_one_or_none()

    ### Get orders by user
    async def get_by_user(
        self, 
        db: AsyncSession, 
        *, 
        user_id: UUID,
        skip: int = 0,
        limit: int = 100
    ) -> List[Order]:
        result = await db.execute(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    ### Get orders by status
    async def get_by_status(
        self, 
        db: AsyncSession, 
        *, 
        status: str,
        skip: int = 0,
        limit: int = 100
    ) -> List[Order]:
        result = await db.execute(
            select(Order)
            .where(Order.status == status)
            .order_by(Order.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

order = CRUDOrder(Order)
