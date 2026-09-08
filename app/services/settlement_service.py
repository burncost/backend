"""Settlement service scaffold (Phase 5 — Settlement & Disbursement, increment 1).

This increment is DELIBERATELY INERT:
- Nothing in the request/order/payment flow calls into this service yet.
- No disbursement (transfer) is executed here.
- Real-money payout automation is deferred until it can be validated on staging
  with test accounts and gateway credentials behind the feature flag.

Purpose of this module today: define the flag-gated, idempotent accrual helper
that a later increment will invoke from the (already flag-checked) capture/delivery
flow, and that the payout runner will read from. Safe to import; does nothing unless
explicitly called with `SETTLEMENT_ENABLED` truthy.
"""
import os
from decimal import Decimal
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.settlement_ledger import VendorSettlementLedger


def is_enabled() -> bool:
    return settings.SETTLEMENT_ENABLED


def commission_rate() -> Decimal:
    try:
        return Decimal(str(settings.SETTLEMENT_COMMISSION_RATE or "0.05"))
    except Exception:
        return Decimal("0")


class SettlementService:
    """Flag-gated accrual/read helpers for the vendor settlement ledger."""

    async def accrue_order(
        self,
        db: AsyncSession,
        *,
        vendor_id,
        order_id,
        gross_amount: Decimal,
        gateway_fee: Decimal = Decimal("0"),
        period: str | None = None,
    ):
        """Idempotently add a pending settlement entry for one order.

        Only accrues when the feature flag is on, and never creates a duplicate
        for the same order. This is not yet wired into any live flow.
        """
        if not is_enabled():
            return None
        existing = (await db.execute(
            select(VendorSettlementLedger.id).where(VendorSettlementLedger.order_id == order_id)
        )).first()
        if existing:
            return None

        gross = Decimal(str(gross_amount or 0))
        fee = Decimal(str(gateway_fee or 0))
        commission = (gross * commission_rate()).quantize(Decimal("0.01"))
        net = gross - fee - commission

        period = period or datetime.utcnow().strftime("%Y-%m")
        entry = VendorSettlementLedger(
            vendor_id=vendor_id,
            order_id=order_id,
            period=period,
            gross_amount=gross,
            gateway_fee=fee,
            commission_amount=commission,
            net_amount=net,
            status="pending",
        )
        db.add(entry)
        await db.flush()
        return entry
