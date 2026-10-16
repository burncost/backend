from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

import logging

from app.core.database import get_db
from app.api.deps import get_current_user, get_current_vendor, get_optional_user
from app.models.product import Product
from app.schemas.promo import (
    PromoCodeCreate, PromoCodeResponse, PromoCodeUpdate,
    PromoValidateRequest, PromoValidateResponse,
)
from app.crud import promo as promo_crud
from app.services import promo_notification_service
from app.services import promo_service
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter()


### Validate a promo code (public)
@router.post("/validate", response_model=PromoValidateResponse)
async def validate_promo(
    req: PromoValidateRequest,
    user=Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    promo = await promo_crud.get_by_code(db, req.code)
    if not promo:
        return PromoValidateResponse(valid=False, discount_amount=0, message="Invalid promo code")

    if not promo.is_active:
        return PromoValidateResponse(valid=False, discount_amount=0, message="This promo code is no longer active")

    if promo.max_uses > 0 and promo.current_uses >= promo.max_uses:
        return PromoValidateResponse(valid=False, discount_amount=0, message="This promo code has reached its usage limit")

    # Per-buyer cap (0 = unlimited). Only checkable for a signed-in user; an
    # anonymous call falls back to the global checks above (the hard stop is at
    # checkout, which always has the authenticated buyer).
    if promo.max_uses_per_user > 0 and user is not None:
        used = await promo_crud.count_user_uses(db, promo.code, user.id)
        if used >= promo.max_uses_per_user:
            return PromoValidateResponse(
                valid=False, discount_amount=0,
                message="You've already used this promo code the maximum number of times",
            )

    if promo.expires_at and promo.expires_at < datetime.utcnow():
        return PromoValidateResponse(valid=False, discount_amount=0, message="This promo code has expired")

    amount = float(promo.discount_amount)
    min_order = float(promo.min_order_amount or 0)

    # Context-free call (no cart sent): keep reporting the code's flat value, as
    # before. Scope/min-order can only be judged against a cart.
    if not req.items and req.subtotal is None:
        return PromoValidateResponse(
            valid=True,
            discount_amount=amount,
            message=f"₦{amount:,.0f} discount applied!",
            min_order_amount=min_order,
        )

    # Cart-aware call: run the exact eligibility rules `orders.create` enforces
    # (scope coverage, minimum order, caps) so a code that checkout would silently
    # drop is flagged here, with the discount capped the same way.
    cart_items = None
    if req.items:
        cart_items = [
            {
                "product_id": i.product_id,
                "vendor_id": i.vendor_id,
                "category_id": i.category_id,
                # Checkout measures coverage in line totals, so do the same.
                "total_price": round(float(i.unit_price) * int(i.quantity), 2),
            }
            for i in req.items
        ]

    discount, reason = promo_service.evaluate(promo, cart_items, subtotal=req.subtotal)
    if reason:
        return PromoValidateResponse(
            valid=False,
            discount_amount=0,
            message=reason,
            min_order_amount=min_order,
        )

    return PromoValidateResponse(
        valid=True,
        discount_amount=discount,
        message=f"₦{discount:,.0f} discount applied!",
        min_order_amount=min_order,
    )


### Create a promo code (vendor only)
@router.post("/", response_model=PromoCodeResponse, status_code=status.HTTP_201_CREATED)
async def create_promo(
    data: PromoCodeCreate,
    background_tasks: BackgroundTasks,
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

    vendor_id = UUID(vendor["id"])

    # Vendors may scope a code to their whole catalogue ("vendor"), a single
    # product of theirs ("product") or a category ("category"). Platform-wide
    # codes are an admin-only concern.
    scope_type = data.scope_type or "vendor"
    if scope_type == "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform admins can create platform-wide promo codes",
        )
    if scope_type not in ("vendor", "product", "category"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid promo scope")

    scope_id = None
    scope_label = None
    if scope_type == "product":
        if not data.scope_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_id (product) is required")
        product = (await db.execute(select(Product).where(Product.id == data.scope_id))).scalar_one_or_none()
        if not product or product.vendor_id != vendor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only target your own products",
            )
        scope_id = data.scope_id
        scope_label = f"on {product.name}"
    elif scope_type == "category":
        if not data.scope_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_id (category) is required")
        scope_id = data.scope_id

    promo = await promo_crud.create(db, data, vendor_id=vendor_id, scope_type=scope_type, scope_id=scope_id)

    # Notify the vendor's own past customers off the request path (own DB session);
    # best-effort — never block promo creation on delivery.
    background_tasks.add_task(
        promo_notification_service.deliver_promo_created,
        promo.id, vendor_id=vendor_id, scope_label=scope_label,
    )

    return promo


### List my promo codes (vendor only)
@router.get("/my", response_model=list[PromoCodeResponse])
async def list_my_promos(
    vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db),
):
    promos = await promo_crud.list_by_vendor(db, UUID(vendor["id"]))
    return promos


### Edit, cancel or reactivate a promo code (vendor only)
@router.patch("/{promo_id}", response_model=PromoCodeResponse)
async def update_promo(
    promo_id: UUID,
    data: PromoCodeUpdate,
    vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db),
):
    promo = await promo_crud.get_by_id(db, promo_id)
    if not promo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promo code not found"
        )
    # Vendors may only manage their own codes.
    if promo.vendor_id is None or str(promo.vendor_id) != str(vendor["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own promo codes",
        )

    vendor_id = UUID(vendor["id"])
    fields = data.model_dump(exclude_unset=True)

    # A rename has to stay unique; codes are stored upper-cased, so compare
    # case-insensitively against the current code before hitting the lookup.
    new_code = fields.get("code")
    if new_code and str(new_code).upper() != promo.code:
        if await promo_crud.get_by_code(db, new_code):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A promo code with this name already exists",
            )

    # Scope re-targeting mirrors create: platform-wide codes stay admin-only and a
    # product scope must point at one of the vendor's own products. Only the keys
    # the caller sent are validated, so a plain cancel skips all of this.
    if "scope_type" in fields:
        scope_type = fields["scope_type"]
        if scope_type == "platform":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only platform admins can manage platform-wide promo codes",
            )
        scope_id = fields.get("scope_id", promo.scope_id)
        if scope_type in ("product", "category") and not scope_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"scope_id ({scope_type}) is required",
            )
        if scope_type == "product":
            product = (await db.execute(select(Product).where(Product.id == scope_id))).scalar_one_or_none()
            if not product or product.vendor_id != vendor_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only target your own products",
                )
        if scope_type == "vendor":
            # A catalogue-wide code carries no target id; clear a stale one left
            # behind by switching away from a product/category scope.
            fields["scope_id"] = None

    updated = await promo_crud.update(db, promo_id, fields)
    return updated


### Delete a promo code (vendor only)
@router.delete("/{promo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_promo(
    promo_id: UUID,
    vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db),
):
    promo = await promo_crud.get_by_id(db, promo_id)
    if not promo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promo code not found"
        )
    # Vendors may only delete their own codes.
    if promo.vendor_id is None or str(promo.vendor_id) != str(vendor["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own promo codes",
        )
    await promo_crud.delete(db, promo_id)
    return None
