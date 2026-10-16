"""Settlement service scaffold (Phase 5 - Settlement & Disbursement, increment 1).

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
from app.services import runtime_settings


def is_enabled() -> bool:
    """True when settlement accrual/disbursement is switched on.

    Reads the DB-backed override first (admins flip it at runtime), then falls
    back to the `SETTLEMENT_ENABLED` config default. Call `refresh_from_db(db)`
    before relying on this in a request path.
    """
    return runtime_settings.get_bool(
        "settlement_enabled", bool(getattr(settings, "SETTLEMENT_ENABLED", False))
    )


async def refresh_from_db(db: AsyncSession) -> None:
    """Reload the cached settlement/pricing overrides from `system_settings`."""
    await runtime_settings.refresh(db)


def commission_rate() -> Decimal:
    """Extra platform commission taken at settlement, as a FRACTION.

    Expressed as a fraction (0.05 = 5%) to stay consistent with how it is applied
    (`gross * rate`). This is deliberately distinct from a category's
    `platform_margin`, which is stored as a PERCENT; `pricing_service` owns that
    conversion. Defaults to 0 because the margin is already infused into the
    selling price at checkout.
    """
    return runtime_settings.get_decimal(
        "settlement_commission_rate",
        _safe_decimal(getattr(settings, "SETTLEMENT_COMMISSION_RATE", "0.0")),
    )


def margin_split() -> tuple[Decimal, Decimal]:
    """(client_share, vendor_share) of the platform margin, normalised to sum to 1.

    `client_share` is added on top of the buyer's item subtotal; `vendor_share` is
    deducted from the vendor's settlement payout.
    """
    client = runtime_settings.get_decimal(
        "client_margin_share", _safe_decimal(getattr(settings, "CLIENT_MARGIN_SHARE", "0.40"))
    )
    vendor = runtime_settings.get_decimal(
        "vendor_margin_share", _safe_decimal(getattr(settings, "VENDOR_MARGIN_SHARE", "0.60"))
    )
    if client < 0:
        client = Decimal("0")
    if vendor < 0:
        vendor = Decimal("0")
    total = client + vendor
    if total <= 0:
        return Decimal("1"), Decimal("0")
    return client / total, vendor / total


def _safe_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
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
        vendor_margin: Decimal = Decimal("0"),
        period: str | None = None,
    ):
        """Idempotently add a pending settlement entry for one order.

        Only accrues when the feature flag is on, and never creates a duplicate
        for the same order.

        `gross_amount` is the captured amount; `gateway_fee` and `commission_amount`
        reduce it to `net_amount`. `vendor_margin` is the vendor's share of the
        platform margin (see `margin_split()`) and is deducted from the payout
        because the buyer was not charged that portion at checkout.
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
        margin = Decimal(str(vendor_margin or 0)).quantize(Decimal("0.01"))
        commission = (gross * commission_rate()).quantize(Decimal("0.01"))
        net = (gross - fee - commission - margin).quantize(Decimal("0.01"))

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
