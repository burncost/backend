"""Admin promotion management.

Platform-wide counterpart to the vendor `/promos` endpoints. Admins (and any
role granted via `require_roles`) create/inspect/disable/delete promo codes
that are NOT owned by a vendor - either `platform` (every product site-wide) or
`category` (every product in a category). Mounted under the `/admin` prefix so
it is reachable from the Admin app.
"""
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from uuid import UUID

import logging

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.category import Category
from app.schemas.promo import PromoCodeCreate, PromoCodeResponse
from app.crud import promo as promo_crud
from app.services import promo_notification_service

logger = logging.getLogger(__name__)

router = APIRouter()

# admin / super_admin are always allowed by require_roles; marketing & manager
# roles also run promotions from the admin panel.
admin_guard = require_roles("marketing", "manager")


class PromoCodeToggle(BaseModel):
    is_active: bool


@router.get("/promos", response_model=list[PromoCodeResponse])
async def admin_list_promos(
    current_user=Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """List every promo code (platform, vendor and scoped codes)."""
    return await promo_crud.list_all(db)


@router.post("/promos", response_model=PromoCodeResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_promo(
    data: PromoCodeCreate,
    background_tasks: BackgroundTasks,
    current_user=Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    existing = await promo_crud.get_by_code(db, data.code)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A promo code with this name already exists",
        )

    scope_type = data.scope_type or "platform"
    if scope_type not in ("platform", "category"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins can only create platform-wide or category promo codes",
        )

    scope_id = None
    scope_label = None
    if scope_type == "category":
        if not data.scope_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_id (category) is required")
        category = (await db.execute(select(Category).where(Category.id == data.scope_id))).scalar_one_or_none()
        if not category:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
        scope_id = data.scope_id
        scope_label = f"on {category.name}"

    # vendor_id is None → platform/admin-owned code, enforced in checkout by scope.
    promo = await promo_crud.create(db, data, vendor_id=None, scope_type=scope_type, scope_id=scope_id)

    # Deliver the new code off the request path: a platform-wide fan-out touches
    # every active user, so it runs as a background task with its own DB session.
    # Best-effort - a delivery hiccup must never block the promo from being created.
    background_tasks.add_task(
        promo_notification_service.deliver_promo_created,
        promo.id, scope_label=scope_label,
    )

    return promo


@router.patch("/promos/{promo_id}", response_model=PromoCodeResponse)
async def admin_toggle_promo(
    promo_id: UUID,
    payload: PromoCodeToggle,
    current_user=Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    promo = await promo_crud.set_active(db, promo_id, payload.is_active)
    if not promo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo code not found")
    return promo


@router.delete("/promos/{promo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_promo(
    promo_id: UUID,
    current_user=Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    deleted = await promo_crud.delete(db, promo_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo code not found")
    return None
