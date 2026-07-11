from typing import Generator, Optional
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import get_current_user_id, get_current_user_id_optional

from app.crud import user as user_crud
from app.crud import vendor as vendor_crud
from app.models.vendor import Vendor

### Get current authenticated user (raises 401 if not authenticated)
async def get_current_user(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    user = await user_crud.get(db, id=user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user


### Get current user or None (allows anonymous access)
async def get_optional_user(
    db: AsyncSession = Depends(get_db),
    user_id: Optional[str] = Depends(get_current_user_id_optional)
):
    if not user_id:
        return None
    user = await user_crud.get(db, id=user_id)
    return user

### Get current active user
async def get_current_active_user(
    current_user = Depends(get_current_user)
):
    # Add additional checks if needed
    return current_user

### Get current vendor (must be a vendor user)
async def get_current_vendor(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # Check if user role is vendor
    if current_user.role not in ["vendor", "admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a vendor. Please register as a vendor first."
        )
    
    # Get vendor profile
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    vendor = result.scalar_one_or_none()
    
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found. Please complete vendor registration."
        )
    
    return {"id": str(vendor.id), "user_id": str(current_user.id), "business_name": vendor.business_name}


### Get current admin user
async def get_current_admin(
    current_user = Depends(get_current_user)
):
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not an admin"
        )
    return current_user
