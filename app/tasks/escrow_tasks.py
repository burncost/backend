"""Escrow auto-release - the clock half of the dispute-resolution policy.

Funds are held for `orders.inspection_window_hours` (48h by default) counted
from verified delivery. Beat runs this every 15 minutes; an order whose window
has expired is settled through `escrow_service.release_order_escrow_now`, which
refuses to pay while a dispute is open or a refund stands.

Legacy rows delivered before the clock existed have `auto_release_at = NULL`;
they are stamped here (delivered_at + window) and settled on the same pass when
the window is already past, so no historical order is stranded.
"""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.celery_app import celery_app
from app.core.database import AsyncSessionLocal
from app.models.order import Order, OrderStatus, PaymentStatus
from app.services import escrow_service

logger = logging.getLogger(__name__)


async def _auto_release(limit: int = 200) -> dict:
    now = datetime.utcnow()
    released = withheld = scheduled = 0

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Order)
                .options(selectinload(Order.items))
                .where(
                    Order.status == OrderStatus.DELIVERED,
                    Order.payment_status == PaymentStatus.COMPLETED,
                    Order.escrow_released_at.is_(None),
                )
                .order_by(Order.auto_release_at.asc().nulls_last())
                .limit(limit)
            )
        ).scalars().all()

        for order in rows:
            if not getattr(order, "auto_release_at", None):
                escrow_service.stamp_inspection_window(order, now=order.delivered_at or now)
                scheduled += 1
            if order.auto_release_at and order.auto_release_at > now:
                continue

            result = await escrow_service.release_order_escrow_now(db, order, commit=False)
            if result.get("released"):
                released += 1
            else:
                withheld += 1

        await db.commit()

    return {
        "checked": len(rows),
        "released": released,
        "withheld": withheld,
        "scheduled": scheduled,
    }


@celery_app.task(name="auto_release_expired_escrows")
def auto_release_expired_escrows(limit: int = 200) -> dict:
    """Release escrow for orders whose inspection window has expired."""
    try:
        result = asyncio.run(_auto_release(limit))
    except Exception as exc:  # one bad order must not kill the beat tick
        logger.error("auto_release_expired_escrows failed: %s", exc)
        return {"error": str(exc)}

    if result["released"] or result["withheld"]:
        logger.info(
            "auto_release_expired_escrows: %s released, %s withheld, %s scheduled (of %s)",
            result["released"], result["withheld"], result["scheduled"], result["checked"],
        )
    return result
