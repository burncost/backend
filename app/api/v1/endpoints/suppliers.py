from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.models.vendor import Vendor
from app.schemas.vendor import VendorResponse, VendorUpdate
from app.api.deps import get_current_admin, get_current_vendor

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


### List verified suppliers
@router.get("/", response_model=List[VendorResponse])
async def list_suppliers(
    verified_only: bool = Query(True),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    query = select(Vendor)

    if verified_only:
        query = query.where(Vendor.verification_status == "verified")

    if search:
        query = query.where(Vendor.business_name.ilike(f"%{search}%"))

    query = query.order_by(Vendor.business_name)
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    suppliers = result.scalars().all()
    return suppliers


### Get supplier by ID
@router.get("/{supplier_id}", response_model=VendorResponse)
async def get_supplier(
    supplier_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Vendor).where(Vendor.id == supplier_id))
    supplier = result.scalar_one_or_none()

    if not supplier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found"
        )

    return supplier


### Update supplier (vendor or admin only)
@router.put("/{supplier_id}", response_model=VendorResponse)
async def update_supplier(
    supplier_id: UUID,
    update_data: VendorUpdate,
    current_user: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Vendor).where(Vendor.id == supplier_id))
    supplier = result.scalar_one_or_none()

    if not supplier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found"
        )

    # Only the vendor themselves or an admin can update
    if str(supplier.user_id) != current_user["id"] and current_user.get("role") not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this supplier"
        )

    # Update fields if provided
    update_dict = update_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(supplier, field, value)

    await db.commit()
    await db.refresh(supplier)

    logger.info(f"Supplier {supplier_id} updated by user {current_user['id']}")
    return supplier


### Delete supplier (admin only)
@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(
    supplier_id: UUID,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Vendor).where(Vendor.id == supplier_id))
    supplier = result.scalar_one_or_none()

    if not supplier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found"
        )

    await db.delete(supplier)
    await db.commit()

    logger.info(f"Supplier {supplier_id} deleted by admin {current_admin['id']}")
    return None
