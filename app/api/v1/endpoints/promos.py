from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_user, get_current_vendor
from app.schemas.promo import (
    PromoCodeCreate, PromoCodeResponse,
    PromoValidateRequest, PromoValidateResponse,
)
from app.crud import promo as promo_crud
from datetime import datetime

router = APIRouter()


### Validate a promo code (public)
@router.post("/validate", response_model=PromoValidateResponse)
async def validate_promo(
    req: PromoValidateRequest,
    db: AsyncSession = Depends(get_db),
):
    promo = await promo_crud.get_by_code(db, req.code)
    if not promo:
        return PromoValidateResponse(valid=False, discount_percent=0, message="Invalid promo code")

    if not promo.is_active:
        return PromoValidateResponse(valid=False, discount_percent=0, message="This promo code is no longer active")

    if promo.max_uses > 0 and promo.current_uses >= promo.max_uses:
        return PromoValidateResponse(valid=False, discount_percent=0, message="This promo code has reached its usage limit")

    if promo.expires_at and promo.expires_at < datetime.utcnow():
        return PromoValidateResponse(valid=False, discount_percent=0, message="This promo code has expired")

    return PromoValidateResponse(
        valid=True,
        discount_percent=float(promo.discount_percent),
        message=f"{float(promo.discount_percent):.0f}% discount applied!"
    )


### Create a promo code (vendor only)
@router.post("/", response_model=PromoCodeResponse, status_code=status.HTTP_201_CREATED)
async def create_promo(
    data: PromoCodeCreate,
    vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db),
):
    # Check if code already exists
    existing = await promo_crud.get_by_code(db, data.code)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A promo code with this name already exists"
        )

    promo = await promo_crud.create(db, data, vendor_id=UUID(vendor["id"]))
    return promo


### List my promo codes (vendor only)
@router.get("/my", response_model=list[PromoCodeResponse])
async def list_my_promos(
    vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db),
):
    promos = await promo_crud.list_by_vendor(db, UUID(vendor["id"]))
    return promos


### Delete a promo code (vendor only)
@router.delete("/{promo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_promo(
    promo_id: UUID,
    vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db),
):
    deleted = await promo_crud.delete(db, promo_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promo code not found"
        )
    return None
