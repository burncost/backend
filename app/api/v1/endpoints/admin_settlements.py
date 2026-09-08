"""Admin Settlement & Disbursement endpoints (Phase 5).

All money-motion actions are gated behind `SETTLEMENT_ENABLED` and admin role.
Capture recording + ledger reads are safe backfill/view operations.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.database import get_db
from app.models.user import User
from app.services import settlement_ops
from app.services.payouts import PayoutService

router = APIRouter()
admin_guard = require_roles("admin", "super_admin")


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
    """Attempt payouts for a gateway — never fires unless flag+keys are configured."""
    gateway = payload.get("gateway", "")
    return await PayoutService().run(db, gateway, payload.get("vendor_id"))
