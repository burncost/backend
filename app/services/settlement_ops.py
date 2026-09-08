"""Settlement operations (Phase 5) — capture recording, flag-gated accrual, and
read summaries backing the admin endpoints. No disbursement here; that lives in
`app.services.payouts`.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.order import Order
from app.models.payment_capture import PaymentCapture
from app.models.settlement_ledger import VendorSettlementLedger
from app.services.settlement_service import SettlementService, is_enabled


async def record_capture(
    db: AsyncSession,
    *,
    order_id: UUID,
    gateway: str,
    amount,
    provider_reference: str | None = None,
    gateway_fee=0,
):
    """Upsert the gateway capture record for an order (one per order). Safe/backfill."""
    from decimal import Decimal
    now = datetime.utcnow()
    row = (await db.execute(select(PaymentCapture).where(PaymentCapture.order_id == order_id))).scalar_one_or_none()
    if not row:
        row = PaymentCapture(order_id=order_id, gateway=gateway, amount=Decimal(str(amount)))
        db.add(row)
    row.gateway = gateway
    row.amount = Decimal(str(amount))
    row.gateway_fee = Decimal(str(gateway_fee or 0))
    row.provider_reference = provider_reference
    row.status = "captured"
    row.captured_at = row.captured_at or now
    await db.commit()
    await db.refresh(row)
    return {
        "order_id": str(row.order_id),
        "gateway": row.gateway,
        "amount": float(row.amount),
        "gateway_fee": float(row.gateway_fee),
        "provider_reference": row.provider_reference,
    }


async def accrue_order(db: AsyncSession, order_id: UUID):
    """Flag-gated: accrue a pending ledger row for an order that has a capture."""
    if not is_enabled():
        return {"ok": False, "reason": "SETTLEMENT_ENABLED is off", "order_id": str(order_id)}
    cap = (await db.execute(select(PaymentCapture).where(PaymentCapture.order_id == order_id))).scalar_one_or_none()
    if not cap:
        return {"ok": False, "reason": "No capture recorded for this order", "order_id": str(order_id)}
    order = (await db.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )).scalar_one_or_none()
    if not order or not order.items:
        return {"ok": False, "reason": "Order/vendor not found", "order_id": str(order_id)}
    vendor_id = order.items[0].vendor_id
    entry = await SettlementService().accrue_order(
        db, vendor_id=vendor_id, order_id=order_id,
        gross_amount=cap.amount, gateway_fee=cap.gateway_fee,
    )
    await db.commit()
    if not entry:
        return {"ok": False, "reason": "Not accrued (duplicate or disabled)", "order_id": str(order_id)}
    return {"ok": True, "order_id": str(order_id), "net_amount": float(entry.net_amount)}


async def pending_summary(db: AsyncSession):
    """Aggregate pending settlement totals per vendor (read-only)."""
    rows = (await db.execute(
        select(
            VendorSettlementLedger.vendor_id,
            func.count(VendorSettlementLedger.id).label("orders"),
            func.sum(VendorSettlementLedger.net_amount).label("total"),
        )
        .where(VendorSettlementLedger.status == "pending")
        .group_by(VendorSettlementLedger.vendor_id)
        .order_by(func.sum(VendorSettlementLedger.net_amount).desc())
    )).all()
    return {
        "vendors": [
            {"vendor_id": str(v), "orders": int(n), "pending_total": float(t or 0)}
            for v, n, t in rows
        ]
    }
