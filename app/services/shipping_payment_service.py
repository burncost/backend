"""Shipping payment service - the separate, post-checkout delivery charge.

Checkout covers goods + fees + VAT only. Once delivery is arranged (a driver bid
accepted or a vendor quote agreed) the buyer pays that delivery fee in its own
transaction through this service, exactly like a token purchase:

1. `build_invoice` snapshots the still-unpaid delivery jobs, creates a
   `ShippingInvoice` (reference = gateway tx_ref) and returns the checkout link.
2. `confirm_reference` is called by the gateway webhook / return-trip verify /
   the mock confirm endpoint. It settles the invoice idempotently and releases
   exactly the jobs it covered (never a job added later).

The invoice amount is always server-computed; the client never supplies it.
"""
import logging
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.delivery import DeliveryJob
from app.models.delivery_event import DeliveryJobEvent
from app.models.notification import Notification
from app.models.order import Order
from app.models.shipping_invoice import (
    INVOICE_COMPLETED,
    INVOICE_FAILED,
    INVOICE_PENDING,
    ShippingInvoice,
)
from app.models.user import User
from app.models.vendor import Vendor
from app.services.bid_dispatch_service import money, unpaid_delivery_total

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")
_SHIPPING_LOG_PREFIX = "SHP-"

# Jobs whose fee is still collectable. Cancelled/expired jobs are excluded, and a
# delivered job still owes its fee (it was simply not collected up front).
_BILLABLE_STATUSES = ("available", "accepted", "picked_up", "in_transit", "delivered")


def _reference() -> str:
    return f"{_SHIPPING_LOG_PREFIX}{uuid.uuid4().hex[:12].upper()}"


def _notify(db: AsyncSession, user_id, title: str, message: str, link: str | None = None) -> None:
    if user_id is None:
        return
    db.add(Notification(user_id=user_id, type="delivery", title=title, message=message,
                        read=False, link=link))


async def billable_jobs(db: AsyncSession, order_id) -> List[DeliveryJob]:
    """Delivery jobs on this order whose fee the buyer still owes."""
    return list((await db.execute(
        select(DeliveryJob).where(
            DeliveryJob.order_id == order_id,
            DeliveryJob.shipping_paid.is_(False),
            DeliveryJob.status.in_(list(_BILLABLE_STATUSES)),
        ).order_by(DeliveryJob.created_at.asc())
    )).scalars().all())


async def delivery_due(db: AsyncSession, order_id) -> Decimal:
    """Total delivery amount currently owed on the order."""
    return unpaid_delivery_total(await billable_jobs(db, order_id))


async def build_invoice(db: AsyncSession, order: Order) -> Dict:
    """Create the delivery payment for everything currently owed and return a link.

    Returns `{"success": False, "error": "<code>"}` for caller errors:
    `order_unpaid` (goods must be paid first), `nothing_due` (no arrangement yet).
    """
    paid = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    if paid != "completed":
        return {"success": False, "error": "order_unpaid",
                "message": "Delivery is paid after the order itself is confirmed."}

    jobs = await billable_jobs(db, order.id)
    total = unpaid_delivery_total(jobs)
    if total <= 0:
        return {"success": False, "error": "nothing_due",
                "message": "No delivery fee is due yet - choose a driver or vendor quote first."}

    reference = _reference()
    user = (await db.execute(select(User).where(User.id == order.user_id))).scalar_one_or_none()

    from app.services.payment_service import PaymentService

    payment = await PaymentService().initialize_payment(
        amount=float(total),
        email=(user.email if user else "") or "",
        reference=reference,
        metadata={
            "type": "shipping_payment",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "user_id": str(order.user_id),
        },
        redirect_url=f"{settings.FRONTEND_URL}/dashboard/orders?shipping_ref={reference}",
    )

    provider = str(payment.get("provider") or "")
    if not payment.get("success"):
        return {
            "success": False,
            "error": "gateway_error",
            "message": payment.get("error") or "Could not start the delivery payment. Try again.",
            "reference": reference,
        }

    invoice = ShippingInvoice(
        order_id=order.id,
        reference=reference,
        amount=total,
        provider=provider or None,
        status=INVOICE_PENDING,
        covered_job_ids=[str(job.id) for job in jobs],
    )
    db.add(invoice)
    order.shipping_payment_status = "pending"
    try:
        await db.flush()
        # Only one live delivery link per order: supersede earlier unpaid ones so
        # the buyer cannot accidentally settle two invoices for the same jobs.
        await db.execute(
            update(ShippingInvoice)
            .where(
                ShippingInvoice.order_id == order.id,
                ShippingInvoice.status == INVOICE_PENDING,
                ShippingInvoice.id != invoice.id,
            )
            .values(status=INVOICE_FAILED)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    except Exception as exc:  # a bookkeeping failure must not hide the checkout link
        await db.rollback()
        logger.error("Could not persist shipping invoice %s: %s", reference, exc)

    return {
        "success": True,
        "reference": reference,
        "amount": float(total),
        "currency": "NGN",
        "payment_url": "" if provider == "mock" else (payment.get("authorization_url") or ""),
        "provider": provider,
        "job_count": len(jobs),
        "description": f"Delivery for order {order.order_number}",
    }


async def confirm_reference(
    db: AsyncSession,
    reference: str,
    *,
    provider: Optional[str] = None,
    paid_amount: Optional[float] = None,
    provider_reference: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict:
    """Settle a delivery invoice once its payment is confirmed.

    Idempotent by reference (conditional UPDATE claim), so a webhook retry or a
    webhook racing the buyer's return trip settles exactly one time. Only the
    jobs snapshotted on the invoice are released, so a delivery arranged after the
    invoice was created is never marked paid by it.

    Statuses: settled | already_settled | already_covered | not_found | forbidden
    | amount_mismatch | stale (arrangement changed after the invoice - payment
    must be re-quoted)
    """
    invoice = (await db.execute(
        select(ShippingInvoice).where(ShippingInvoice.reference == reference).with_for_update()
    )).scalar_one_or_none()
    if invoice is None:
        logger.warning("Shipping settlement skipped: unknown reference %s", reference)
        return {"status": "not_found", "reference": reference}

    order = (await db.execute(select(Order).where(Order.id == invoice.order_id))).scalar_one_or_none()
    if order is None:
        return {"status": "not_found", "reference": reference}
    if user_id and str(order.user_id) != str(user_id):
        logger.warning("Shipping settlement refused: %s is not %s's order", reference, user_id)
        return {"status": "forbidden", "reference": reference}

    if invoice.status == INVOICE_COMPLETED:
        return {"status": "already_settled", "reference": reference,
                "amount": float(invoice.amount or 0)}

    if paid_amount is not None and abs(float(paid_amount) - float(invoice.amount or 0)) > 0.01:
        logger.error("Shipping settlement refused: gateway reported %.2f for %s but %.2f was quoted",
                     float(paid_amount), reference, float(invoice.amount or 0))
        return {"status": "amount_mismatch", "reference": reference}

    job_ids = []
    for raw in (invoice.covered_job_ids or []):
        try:
            job_ids.append(UUID(str(raw)))
        except (ValueError, TypeError):
            continue

    jobs = list((await db.execute(
        select(DeliveryJob).where(
            DeliveryJob.id.in_(job_ids),
            DeliveryJob.shipping_paid.is_(False),
            DeliveryJob.status.in_(list(_BILLABLE_STATUSES)),
        )
    )).scalars().all()) if job_ids else []

    if job_ids and not jobs:
        # Every job this invoice covered was already settled by another invoice
        # (the buyer opened two gateway links, or a retry pointed at an old one).
        # Completing it would release nothing, so it is flagged for refund instead
        # of being silently booked as a delivery payment.
        logger.error("Shipping settlement refused: %s covers only already-paid jobs", reference)
        return {"status": "already_covered", "reference": reference}

    pending_total = unpaid_delivery_total(jobs)
    if pending_total > money(invoice.amount):
        # A fee increased after the invoice was built: charging would underpay.
        logger.error("Shipping settlement refused: %s quotes %.2f but %.2f is owed now",
                     reference, float(invoice.amount or 0), float(pending_total))
        return {"status": "stale", "reference": reference}

    claimed = await db.execute(
        update(ShippingInvoice)
        .where(ShippingInvoice.id == invoice.id, ShippingInvoice.status != INVOICE_COMPLETED)
        .values(
            status=INVOICE_COMPLETED,
            completed_at=datetime.utcnow(),
            provider=provider or invoice.provider,
            provider_reference=provider_reference or invoice.provider_reference,
            paid_amount=(
                invoice.amount if paid_amount is None
                else Decimal(str(paid_amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
            ),
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        logger.info("Shipping settlement: %s was already settled concurrently", reference)
        return {"status": "already_settled", "reference": reference}

    now = datetime.utcnow()
    settled_fee = Decimal("0.00")
    for job in jobs:
        job.shipping_paid = True
        job.shipping_invoice_id = invoice.id
        settled_fee += money(job.delivery_fee)
        db.add(DeliveryJobEvent(
            delivery_job_id=job.id,
            order_id=job.order_id,
            actor_type="system",
            event_type="shipping_paid",
            status=job.status,
            note=f"Delivery fee paid (ref: {reference}).",
        ))
    await db.flush()

    # Order-level rollup: anything still owing keeps the buyer on "pending".
    remaining = await delivery_due(db, order.id)
    all_jobs = list((await db.execute(
        select(DeliveryJob).where(
            DeliveryJob.order_id == order.id,
            DeliveryJob.status.notin_(["cancelled", "expired"]),
        )
    )).scalars().all())
    fee_total = Decimal("0.00")
    for job in all_jobs:
        fee_total += money(job.delivery_fee)
    order.delivery_fee_total = money(fee_total)
    if remaining <= 0 and all_jobs:
        order.shipping_payment_status = "paid"
        order.shipping_paid_at = now
    else:
        order.shipping_payment_status = "pending"

    _notify(db, order.user_id, "Delivery payment confirmed",
            f"₦{float(settled_fee):,.2f} delivery payment received for order "
            f"{order.order_number}. The driver can now pick up your goods.",
            "/dashboard/orders")

    driver_ids = [j.driver_profile_id for j in jobs if j.driver_profile_id]
    if driver_ids:
        from app.models.driver import DriverProfile
        drivers = (await db.execute(
            select(DriverProfile).where(DriverProfile.id.in_(driver_ids))
        )).scalars().all()
        for driver in drivers:
            _notify(db, driver.user_id, "Delivery fee paid",
                    f"Order {order.order_number}: the buyer paid the delivery fee. "
                    f"You can proceed with pickup.", "/driver/dashboard")

    vendor_ids = {str(j.vendor_id) for j in jobs}
    if vendor_ids:
        vendors = (await db.execute(
            select(Vendor).where(Vendor.id.in_([UUID(v) for v in vendor_ids]))
        )).scalars().all()
        for vendor in vendors:
            _notify(db, vendor.user_id, "Delivery fee paid",
                    f"Order {order.order_number}: the buyer paid ₦{float(settled_fee):,.2f} "
                    f"for delivery.", "/dashboard/deliveries")

    await db.commit()
    logger.info("Shipping settlement: %s settled %.2f covering %s job(s)",
                reference, float(settled_fee), len(jobs))
    return {
        "status": "settled",
        "reference": reference,
        "amount": float(settled_fee),
        "order_id": str(order.id),
        "order_number": order.order_number,
        "job_count": len(jobs),
        "remaining": float(remaining),
    }


async def pending_invoice(db: AsyncSession, order_id) -> Optional[ShippingInvoice]:
    """Newest still-pending invoice for an order (UI resume helper)."""
    return (await db.execute(
        select(ShippingInvoice)
        .where(ShippingInvoice.order_id == order_id, ShippingInvoice.status == INVOICE_PENDING)
        .order_by(ShippingInvoice.created_at.desc())
    )).scalars().first()


async def mark_failed(db: AsyncSession, reference: str) -> None:
    """Flag an invoice whose gateway checkout failed (best-effort, no commit)."""
    await db.execute(
        update(ShippingInvoice)
        .where(ShippingInvoice.reference == reference,
               ShippingInvoice.status == INVOICE_PENDING)
        .values(status=INVOICE_FAILED)
        .execution_options(synchronize_session=False)
    )

