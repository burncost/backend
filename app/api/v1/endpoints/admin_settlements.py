"""Admin Settlement & Disbursement endpoints (Phase 5).

All money-motion actions are gated behind `SETTLEMENT_ENABLED` and admin role.
Capture recording + ledger reads are safe backfill/view operations.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.database import get_db
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.services import settlement_ops
from app.services import runtime_settings
from app.services.payouts import (
    PayoutService,
    VALID_GATEWAYS,
    configured as gateway_configured,
    gateway_preference,
)

router = APIRouter()
admin_guard = require_roles("admin", "super_admin")

#: Settlement settings owned by this screen. Any other key is rejected so a typo
#: cannot silently write an unrelated system setting.
SETTLEMENT_SETTING_KEYS = (
    "settlement_enabled",
    "settlement_commission_rate",
    "payout_primary_gateway",
    "payout_backup_gateway",
)

SETTLEMENT_SECTION = "settlements"


class CaptureRecord(BaseModel):
    order_id: UUID
    gateway: str = Field(..., pattern="^(flutterwave|paystack|monnify|mock)$")
    amount: float = Field(..., gt=0)
    provider_reference: str | None = None
    gateway_fee: float = Field(0, ge=0)


@router.post("/settlements/captures", dependencies=[Depends(admin_guard)])
async def admin_record_capture(
    payload: CaptureRecord,
    db: AsyncSession = Depends(get_db),
):
    """Backfill / record the gateway capture for an order (safe, no money)."""
    try:
        result = await settlement_ops.record_capture(
            db, order_id=payload.order_id, gateway=payload.gateway,
            amount=payload.amount, provider_reference=payload.provider_reference,
            gateway_fee=payload.gateway_fee,
        )
    except Exception as exc:  # surface FK/type errors cleanly
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"ok": True, "capture": result}


@router.post("/settlements/accrue/{order_id}", dependencies=[Depends(admin_guard)])
async def admin_accrue_order(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Flag-gated: accrue a pending settlement entry for a captured order."""
    result = await settlement_ops.accrue_order(db, order_id)
    if not result.get("ok"):
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=result.get("reason", "not accrued"))
    return result


@router.get("/settlements/summary", dependencies=[Depends(admin_guard)])
async def admin_settlement_summary(db: AsyncSession = Depends(get_db)):
    return await settlement_ops.pending_summary(db)


@router.get("/settlements/payouts/preview", dependencies=[Depends(admin_guard)])
async def admin_payout_preview(
    gateway: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await PayoutService().preview(db, gateway)


@router.post("/settlements/payouts/run", dependencies=[Depends(admin_guard)])
async def admin_payout_run(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Attempt payouts for a gateway - never fires unless flag+keys are configured.

    Omitting ``gateway`` uses the admin-configured primary (Paystack) and falls
    back to the backup (Monnify) when the primary is unconfigured.
    """
    return await PayoutService().run(db, payload.get("gateway"), payload.get("vendor_id"))


# ── Settlement configuration (Phase 7) ──────────────────────────────────────

class SettlementConfigUpdate(BaseModel):
    settlement_enabled: bool | None = None
    settlement_commission_rate: float | None = Field(None, ge=0, le=1)
    payout_primary_gateway: str | None = None
    payout_backup_gateway: str | None = None


def _config_view(db_enabled: bool) -> dict:
    primary, backup = gateway_preference()
    return {
        "settings": runtime_settings.snapshot(),
        "effective": {
            "settlement_enabled": db_enabled,
            "commission_rate": str(runtime_settings.get_decimal("settlement_commission_rate")),
            "payout_primary_gateway": primary,
            "payout_backup_gateway": backup,
        },
        "gateways": [
            {"gateway": gw, "configured": gateway_configured(gw), "role": (
                "primary" if gw == primary else "backup" if gw == backup else "alternative"
            )}
            for gw in VALID_GATEWAYS
        ],
        "available_gateways": list(VALID_GATEWAYS),
    }


@router.get("/settlements/config", dependencies=[Depends(admin_guard)])
async def admin_get_settlement_config(db: AsyncSession = Depends(get_db)):
    """Current settlement switches + payout provider readiness."""
    await runtime_settings.refresh(db, force=True)
    from app.services.settlement_service import is_enabled as _is_enabled
    return _config_view(_is_enabled())


@router.put("/settlements/config", dependencies=[Depends(admin_guard)])
async def admin_update_settlement_config(
    payload: SettlementConfigUpdate,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Update the settlement switches. Only `SETTLEMENT_SETTING_KEYS` can be written."""
    updates: dict[str, str] = {}
    data = payload.model_dump(exclude_none=True)

    for key, value in data.items():
        if key not in SETTLEMENT_SETTING_KEYS:
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=f"Unknown setting '{key}'")
        if key in ("payout_primary_gateway", "payout_backup_gateway"):
            if str(value).lower() not in VALID_GATEWAYS:
                raise HTTPException(
                    http_status.HTTP_400_BAD_REQUEST,
                    detail=f"gateway must be one of {list(VALID_GATEWAYS)}",
                )
            updates[key] = str(value).lower()
        elif key == "settlement_enabled":
            updates[key] = "true" if value else "false"
        else:
            updates[key] = str(value)

    if not updates:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="No settings supplied")

    actor = str(getattr(current_user, "id", "admin") or "admin")
    from datetime import datetime
    now = datetime.utcnow()
    for key, value in updates.items():
        row = (await db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )).scalar_one_or_none()
        if row:
            row.value = value
            row.section = SETTLEMENT_SECTION
            row.updated_by = actor
            row.updated_at = now
        else:
            db.add(SystemSetting(
                key=key, value=value, section=SETTLEMENT_SECTION,
                description="Settlement & disbursement setting", updated_by=actor,
            ))
    await db.commit()

    runtime_settings.invalidate()
    await runtime_settings.refresh(db, force=True)

    from app.services.settlement_service import is_enabled as _is_enabled
    view = _config_view(_is_enabled())
    view["updated"] = list(updates.keys())
    return view
