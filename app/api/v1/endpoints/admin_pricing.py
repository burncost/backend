"""Admin pricing endpoints (Phase 9) — category margin/fee-model editor + VAT and
margin-split configuration.

`categories.py` is a read-only public surface, so the write path for per-category
pricing lives here behind an admin-only guard. All money-affecting switches are
restricted to admin/super_admin (matching settlements).
"""
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.database import get_db
from app.models.category import Category
from app.models.system_setting import SystemSetting
from datetime import datetime

router = APIRouter()
pricing_guard = require_roles("admin", "super_admin")

VALID_FEE_MODELS = ("percentage", "fixed", "service")
VALID_VAT_BASES = ("subtotal_plus_margin", "subtotal")

#: Pricing settings this screen owns (see app.services.runtime_settings).
PRICING_SETTING_KEYS = (
    "vat_enabled",
    "vat_rate",
    "vat_base",
    "client_margin_share",
    "vendor_margin_share",
)
PRICING_SECTION = "pricing"


class CategoryFeeUpdate(BaseModel):
    platform_margin: Optional[float] = Field(None, ge=0, le=100)
    fee_model: Optional[str] = None
    fee_fixed: Optional[float] = Field(None, ge=0)


class PricingConfigUpdate(BaseModel):
    vat_enabled: Optional[bool] = None
    vat_rate: Optional[float] = Field(None, ge=0, le=100)
    vat_base: Optional[str] = None
    client_margin_share: Optional[float] = Field(None, ge=0, le=1)
    vendor_margin_share: Optional[float] = Field(None, ge=0, le=1)


def _serialize_category(cat: Category) -> dict:
    return {
        "id": str(cat.id),
        "name": cat.name,
        "slug": cat.slug,
        "parent_id": str(cat.parent_id) if cat.parent_id else None,
        "division": cat.division,
        "default_unit": cat.default_unit,
        "platform_margin": float(cat.platform_margin) if cat.platform_margin is not None else 0.0,
        "fee_model": cat.fee_model or "percentage",
        "fee_fixed": float(cat.fee_fixed) if cat.fee_fixed is not None else None,
        "is_active": cat.is_active,
    }


@router.get("/pricing/categories", dependencies=[Depends(pricing_guard)])
async def admin_list_pricing_categories(
    parent_only: bool = Query(False),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Categories with their effective fee model + margin (for the 20-row editor)."""
    query = select(Category)
    if active_only:
        query = query.where(Category.is_active.is_(True))
    if parent_only:
        query = query.where(Category.parent_id.is_(None))
    query = query.order_by(Category.display_order, Category.name)
    rows = (await db.execute(query)).scalars().all()
    return {"categories": [_serialize_category(c) for c in rows], "fee_models": list(VALID_FEE_MODELS)}


@router.put("/pricing/categories/{category_id}", dependencies=[Depends(pricing_guard)])
async def admin_update_category_fee(
    category_id: str,
    payload: CategoryFeeUpdate,
    current_user=Depends(pricing_guard),
    db: AsyncSession = Depends(get_db),
):
    """Update one category's platform margin / fee model / fixed fee."""
    try:
        uid = UUID(category_id)
    except ValueError:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Invalid category ID")

    cat = (await db.execute(select(Category).where(Category.id == uid))).scalar_one_or_none()
    if not cat:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Category not found")

    data = payload.model_dump(exclude_none=True)
    if "fee_model" in data:
        model = str(data["fee_model"]).strip().lower()
        if model not in VALID_FEE_MODELS:
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                detail=f"fee_model must be one of {list(VALID_FEE_MODELS)}",
            )
        cat.fee_model = model

    if "platform_margin" in data:
        cat.platform_margin = Decimal(str(data["platform_margin"])).quantize(Decimal("0.01"))

    if "fee_fixed" in data:
        cat.fee_fixed = Decimal(str(data["fee_fixed"])).quantize(Decimal("0.01"))

    # A fixed-fee model needs an amount to be meaningful; guard against a silent
    # "0 margin" category that would pay the platform nothing.
    effective_model = (cat.fee_model or "percentage").lower()
    if effective_model == "fixed" and (cat.fee_fixed is None or Decimal(cat.fee_fixed) <= 0):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail="fee_fixed must be greater than 0 when fee_model is 'fixed'",
        )

    cat.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(cat)
    return {"ok": True, "category": _serialize_category(cat)}


@router.get("/pricing/config", dependencies=[Depends(pricing_guard)])
async def admin_get_pricing_config(db: AsyncSession = Depends(get_db)):
    """VAT + margin-split configuration currently in effect."""
    from app.services import runtime_settings
    from app.services import pricing_service
    from app.services.settlement_service import margin_split

    await runtime_settings.refresh(db, force=True)
    cfg = pricing_service.load_pricing_config()
    client_share, vendor_share = margin_split()
    return {
        "settings": runtime_settings.snapshot(),
        "effective": {
            "vat_enabled": cfg.vat_enabled,
            "vat_rate": pricing_service.f2(cfg.vat_rate),
            "vat_base": cfg.vat_base,
            "client_margin_share": pricing_service.f2(client_share),
            "vendor_margin_share": pricing_service.f2(vendor_share),
        },
        "available_vat_bases": list(VALID_VAT_BASES),
    }


@router.put("/pricing/config", dependencies=[Depends(pricing_guard)])
async def admin_update_pricing_config(
    payload: PricingConfigUpdate,
    current_user=Depends(pricing_guard),
    db: AsyncSession = Depends(get_db),
):
    """Update VAT + margin-split settings (hot paths pick these up within the TTL)."""
    from app.services import runtime_settings

    data = payload.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="No settings supplied")

    updates: dict[str, str] = {}
    for key, value in data.items():
        if key == "vat_base":
            base = str(value).strip().lower()
            if base not in VALID_VAT_BASES:
                raise HTTPException(
                    http_status.HTTP_400_BAD_REQUEST,
                    detail=f"vat_base must be one of {list(VALID_VAT_BASES)}",
                )
            updates[key] = base
        elif key == "vat_enabled":
            updates[key] = "true" if value else "false"
        else:
            updates[key] = str(value)

    # The two margin shares must not both be zero, otherwise the platform earns
    # nothing on a sale.
    try:
        client = Decimal(updates.get("client_margin_share", "0.40"))
        vendor = Decimal(updates.get("vendor_margin_share", "0.60"))
    except InvalidOperation:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Invalid margin share")
    if (client + vendor) <= 0:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail="client_margin_share + vendor_margin_share must be greater than 0",
        )

    actor = str(getattr(current_user, "id", "admin") or "admin")
    now = datetime.utcnow()
    for key, value in updates.items():
        row = (await db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )).scalar_one_or_none()
        if row:
            row.value = value
            row.section = PRICING_SECTION
            row.updated_by = actor
            row.updated_at = now
        else:
            db.add(SystemSetting(
                key=key, value=value, section=PRICING_SECTION,
                description="Pricing configuration setting", updated_by=actor,
            ))
    await db.commit()

    runtime_settings.invalidate()
    await runtime_settings.refresh(db, force=True)
    return {"updated": list(updates.keys()), "settings": runtime_settings.snapshot()}
