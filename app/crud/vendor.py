from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.crud.base import CRUDBase
from app.models.vendor import Vendor
from app.schemas.vendor import VendorCreate, VendorUpdate


class CRUDVendor(CRUDBase[Vendor, VendorCreate, VendorUpdate]):
    ### Get vendor by user ID
    async def get_by_user_id(self, db: AsyncSession, *, user_id: UUID) -> Optional[Vendor]:
        result = await db.execute(select(Vendor).where(Vendor.user_id == user_id))
        return result.scalar_one_or_none()

    ### Get vendor by business registration number
    async def get_by_business_registration(
        self, 
        db: AsyncSession, 
        *, 
        registration_number: str
    ) -> Optional[Vendor]:
        result = await db.execute(
            select(Vendor).where(Vendor.cac_business_registration_number == registration_number)
        )
        return result.scalar_one_or_none()

vendor = CRUDVendor(Vendor)
