"""Refund records for dispute resolutions.

No gateway refund call is wired yet (PaymentService only exposes
initialize/verify/create_transfer), so a resolution *records* the refund and
moves the order's payment status. The admin escrow screen then marks the row
`paid` once the money has actually been sent, which keeps the decision, the
amount and the payout reference auditable.
"""
import logging
from decimal import Decimal

from sqlalchemy import select

from app.models.order import PaymentStatus
from app.models.refund import Refund

logger = logging.getLogger(__name__)


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


async def refunded_total(db, order_id) -> Decimal:
    """Sum of refunds still standing (pending or paid) for an order."""
    if db is None:
        return Decimal("0.00")
    rows = (await db.execute(
        select(Refund.amount).where(
            Refund.order_id == order_id,
            Refund.status.in_(("pending", "paid")),
        )
    )).scalars().all()
    return sum((_money(r) for r in rows), Decimal("0.00"))


async def record_refund(
    db,
    *,
    order,
    amount,
    dispute_id=None,
    reason: str = "",
    requested_by: str | None = None,
    gateway: str | None = None,
) -> Refund | None:
    """Record a refund against an order and update its payment status.

    The order is left `partially_refunded` unless the cumulative refund covers
    the order total, in which case it becomes `refunded` (and `status` follows).
    """
    amount = _money(amount)
    if amount <= 0:
        return None

    row = Refund(
        order_id=order.id,
        dispute_id=dispute_id,
        amount=amount,
        reason=reason or None,
        gateway=gateway or "manual",
        status="pending",
        requested_by=requested_by,
    )
    db.add(row)
    await db.flush()

    total = _money(getattr(order, "total_amount", 0))
    cumulative = await refunded_total(db, order.id)
    if total > 0 and cumulative >= total:
        order.payment_status = PaymentStatus.REFUNDED
        order.status = "refunded"
    elif total > 0:
        order.payment_status = PaymentStatus.PARTIALLY_REFUNDED
    return row


async def has_refund(db, order_id) -> bool:
    if db is None or order_id is None:
        return False
    row = await db.execute(
        select(Refund.id).where(
            Refund.order_id == order_id,
            Refund.status.in_(("pending", "paid")),
        ).limit(1)
    )
    return row.first() is not None
