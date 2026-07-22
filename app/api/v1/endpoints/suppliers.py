from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.models.vendor import Vendor
from app.models.product import Product
from app.models.category import Category
from app.schemas.vendor import VendorResponse, VendorUpdate
from app.api.deps import get_current_admin, get_current_vendor
from pydantic import BaseModel

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class SupplierListItem(BaseModel):
    id: str
    business_name: str
    business_type: str | None = None
    business_address: str | None = None
    city: str | None = None
    state: str | None = None
    business_image: str | None = None
    verification_status: str
    commission_rate: float = 0.0
    rating: float = 0.0
    total_reviews: int = 0
    total_sales: float = 0.0
    is_featured: bool = False
    delivery_time: str = "1-3 Days"
    response_time: str = "< 1 hour"
    specializations: list[str] = []
    categories: list[str] = []
    platform_margin: float = 5.0

    class Config:
        from_attributes = True


### List verified suppliers
@router.get("/", response_model=list[SupplierListItem])
async def list_suppliers(
    verified_only: bool = Query(True),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    query = select(Vendor).options(selectinload(Vendor.products).selectinload(Product.category))

    if verified_only:
        query = query.where(Vendor.verification_status == "verified")

    if search:
        query = query.where(Vendor.business_name.ilike(f"%{search}%"))

    query = query.order_by(Vendor.business_name)
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    suppliers = result.scalars().all()

    # Build typed response with categories and platform margins derived from products
    result_list = []
    for s in suppliers:
        cat_names = list(set(
            p.category.name for p in s.products
            if p.category and p.category.name
        )) if s.products else []
        # Calculate average platform margin from vendor's product categories
        margins = [
            float(p.category.platform_margin) for p in s.products
            if p.category and p.category.platform_margin
        ] if s.products else []
        avg_margin = round(sum(margins) / len(margins), 2) if margins else 5.0

        result_list.append(SupplierListItem(
            id=str(s.id),
            business_name=s.business_name,
            business_type=s.business_type,
            business_address=s.business_address,
            city=s.city,
            state=s.state,
            business_image=s.business_image,
            verification_status=s.verification_status,
            commission_rate=float(s.commission_rate) if s.commission_rate else 0.0,
            rating=float(s.rating) if s.rating else 0.0,
            total_reviews=s.total_reviews or 0,
            total_sales=float(s.total_sales) if s.total_sales else 0.0,
            is_featured=s.is_featured or False,
            delivery_time=s.delivery_time or "1-3 Days",
            response_time=s.response_time or "< 1 hour",
            specializations=s.specializations or [],
            categories=cat_names,
            platform_margin=avg_margin,
        ))

    return result_list


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
