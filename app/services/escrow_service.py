"""Escrow release guard + the delivery inspection clock (dispute resolution).

The published dispute-resolution policy holds funds for a 48-hour inspection
window counted from verified delivery, and forbids paying the supplier while a
dispute is open. Before this module, every release path (buyer confirms
delivery, admin accepts the POD on the buyer's behalf, driver marks delivered)
transferred to the vendor immediately and none of them looked at disputes.

Every release now funnels through this module:

  * `release_order_escrow(...)` - request/background path (FastAPI BackgroundTasks)
  * `release_order_escrow_now(...)` - awaits the transfers (Celery auto-release,
    admin dispute resolution)

Both call `release_block_reason()` first, so the hold cannot be bypassed by a
caller that forgets it.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.models.dispute import Dispute
from app.services import pricing_service as _pricing

logger = logging.getLogger(__name__)

# Policy statuses that keep the escrow frozen.
ACTIVE_DISPUTE_STATUSES = ("open", "in_review", "escalated")

# Order / payment states whose money must not move to the vendor again.
_BLOCKED_ORDER_STATUSES = ("cancelled", "refunded")
_REFUNDED_PAYMENT_STATUSES = ("refunded", "partially_refunded")


def _v(value):
    """Enum-or-string -> plain string (`Order.status` is a SQLEnum)."""
    return getattr(value, "value", value)


def inspection_window_hours(order) -> int:
    hours = getattr(order, "inspection_window_hours", None) or settings.INSPECTION_WINDOW_HOURS
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = 48
    return hours if hours > 0 else 48


def extension_hours() -> int:
    """Size of the single buyer-requested extension (policy default: 24h)."""
    try:
        hours = int(settings.INSPECTION_EXTENSION_HOURS)
    except (TypeError, ValueError):
        hours = 24
    return hours if hours > 0 else 24


def stamp_inspection_window(order, now: datetime | None = None) -> datetime:
    """Start the inspection clock on a delivered order.

    Idempotent within a delivery: an already-scheduled deadline is preserved so
    a repeated "confirm delivery" cannot shorten the buyer's window.
    """
    if getattr(order, "auto_release_at", None):
        return order.auto_release_at
    start = getattr(order, "delivered_at", None) or now or datetime.utcnow()
    order.inspection_window_hours = inspection_window_hours(order)
    order.auto_release_at = start + timedelta(hours=order.inspection_window_hours)
    return order.auto_release_at


async def order_has_open_dispute(db, order_id) -> bool:
    """True when the order has a dispute that must freeze the escrow.

    Fails closed: a lookup error blocks the release rather than paying a vendor
    during an unresolved dispute.
    """
    if db is None or order_id is None:
        return False
    try:
        row = await db.execute(
            select(Dispute.id)
            .where(
                Dispute.order_id == order_id,
                Dispute.status.in_(ACTIVE_DISPUTE_STATUSES),
            )
            .limit(1)
        )
        return row.first() is not None
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Dispute lookup failed for order %s: %s", order_id, exc)
        return True


async def release_block_reason(
    db,
    order,
    *,
    ignore_dispute: bool = False,
    ignore_refund: bool = False,
):
    """Reason the escrow must NOT be released, or None when it may.

    `ignore_*` is used only by the admin dispute resolution, which has already
    decided the split and is executing it deliberately.
    """
    if getattr(order, "escrow_released_at", None):
        return "already_released"
    if _v(getattr(order, "status", None)) in _BLOCKED_ORDER_STATUSES:
        return "order_not_releasable"
    if (not ignore_refund) and _v(getattr(order, "payment_status", None)) in _REFUNDED_PAYMENT_STATUSES:
        return "order_refunded"
    if (not ignore_dispute) and await order_has_open_dispute(db, getattr(order, "id", None)):
        return "dispute_hold"
    return None


async def _safe_deductions(db, order) -> dict:
    """Per-line vendor-borne margin shares; a pricing failure never blocks pay."""
    try:
        return await _pricing.vendor_margins_by_item(db, getattr(order, "items", []) or [])
    except Exception as exc:
        logger.error(
            "Vendor margin recompute failed for order %s: %s",
            getattr(order, "order_number", "?"), exc,
        )
        return {}


def release_plan(order, deductions: dict, scale: float = 1.0) -> list:
    """Per-line transfer arguments: line total minus the vendor margin share.

    `scale` lets a partially-refunded order release only the remainder
    (1 - refunded/total) instead of the full line value.
    """
    plan = []
    for index, item in enumerate(getattr(order, "items", []) or []):
        if not getattr(item, "vendor_id", None):
            continue
        gross = _pricing.f2(item.total_price)
        deduction = min(_pricing.f2(deductions.get(_pricing.item_key(item, index), 0)), gross)
        net = round((gross - deduction) * scale, 2)
        if net <= 0 and scale < 1.0:
            continue
        plan.append({
            "vendor_id": str(item.vendor_id),
            "order_number": order.order_number,
            # Only the net leaves escrow; the retained margin stays on the platform.
            "amount": max(net, 0.0),
            "gross_amount": round(gross * scale, 2),
            "platform_deduction": round(deduction * scale, 2) if scale < 1.0 else deduction,
        })
    return plan


def _transfer_fn():
    """Late import avoids an import cycle and keeps the existing
    `monkeypatch.setattr(orders, "PaymentService", ...)` tests valid."""
    from app.api.v1.endpoints.orders import _release_escrow_to_vendor
    return _release_escrow_to_vendor


async def release_order_escrow(background_tasks, order, db) -> dict:
    """Queue the escrow release for every vendor line, honouring the hold."""
    reason = await release_block_reason(db, order)
    if reason:
        logger.warning(
            "Escrow release withheld for order %s: %s",
            getattr(order, "order_number", "?"), reason,
        )
        return {"released": False, "reason": reason, "order_number": getattr(order, "order_number", None)}

    plan = release_plan(order, await _safe_deductions(db, order))
    transfer = _transfer_fn()
    for entry in plan:
        background_tasks.add_task(transfer, db_session=db, **entry)

    # Stamped in the caller's transaction; background tasks run afterwards.
    order.escrow_released_at = datetime.utcnow()
    if db is not None:
        try:
            await db.commit()
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to stamp escrow release for order %s: %s", order.order_number, exc)
    return {"released": True, "lines": len(plan), "order_number": getattr(order, "order_number", None)}


async def release_order_escrow_now(
    db,
    order,
    *,
    force: bool = False,
    scale: float = 1.0,
    commit: bool = True,
) -> dict:
    """Release inline (Celery auto-release and admin dispute resolution)."""
    reason = await release_block_reason(
        db, order, ignore_dispute=force, ignore_refund=force,
    )
    if reason:
        logger.warning(
            "Escrow release withheld for order %s: %s",
            getattr(order, "order_number", "?"), reason,
        )
        return {"released": False, "reason": reason, "order_number": getattr(order, "order_number", None)}

    plan = release_plan(order, await _safe_deductions(db, order), scale=scale)
    transfer = _transfer_fn()
    for entry in plan:
        await transfer(db_session=db, **entry)

    if scale >= 1.0:
        # Partial releases stay open: the remainder is still owed to the vendor
        # until a later decision, so the idempotency stamp is not set.
        order.escrow_released_at = datetime.utcnow()
    if commit and db is not None:
        await db.commit()
    return {
        "released": True,
        "lines": len(plan),
        "amount": round(sum(entry["amount"] for entry in plan), 2),
        "order_number": getattr(order, "order_number", None),
    }

