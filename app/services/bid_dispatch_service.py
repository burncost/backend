"""Post-confirmation delivery arrangement: job creation, driver bids, vendor quotes.

Every confirmed payment produces ONE `bid` delivery job per vendor group in the
order (see `create_bid_jobs_for_order`, called from payments.py). A bid job has a
48h window during which:

- drivers place a price bid (`place_bid`), and/or
- the vendor offers to deliver it themselves at a quoted fee (`apply_vendor_quote`).

The buyer accepts one bid (`accept_bid`) or the vendor quote, then pays the
delivery invoice (shipping_payment_service). Nothing is auto-awarded and nobody
is auto-charged: the buyer's payment is the acceptance for a vendor quote, and an
accepted bid is explicitly selected.

Rejecting the vendor quote cancels that job and re-opens a fresh bid job so the
buyer still gets a delivery option (`decline_vendor_delivery`).
"""
import logging
import random
import string
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Sequence

from fastapi import HTTPException, status as http_status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.address import CustomerAddress
from app.models.delivery import DeliveryJob
from app.models.delivery_bid import (
    BID_ACCEPTED,
    BID_EXPIRED,
    BID_PENDING,
    BID_REJECTED,
    BID_WITHDRAWN,
    DeliveryBid,
)
from app.models.delivery_event import DeliveryJobEvent
from app.models.driver import DriverProfile
from app.models.notification import Notification
from app.models.order import Order, OrderItem
from app.models.vendor import Vendor
from app.models.vendor_address import VendorAddress

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")

# Bidding window: 48h from confirmation, extendable by 24h on request.
BID_WINDOW_HOURS = int(getattr(settings, "DELIVERY_BID_WINDOW_HOURS", 48) or 48)
EXTENSION_HOURS = int(getattr(settings, "DELIVERY_BID_EXTENSION_HOURS", 24) or 24)

# Safe absolute ceiling for a single vendor transport quote (Naira).
MAX_DELIVERY_FEE = Decimal(str(getattr(settings, "DELIVERY_FEE_MAX", 500_000) or 500_000))
# Optional sanity check against the shipping estimate: 0 disables it.
QUOTE_ESTIMATE_MULTIPLIER = Decimal(
    str(getattr(settings, "DELIVERY_FEE_MAX_MULTIPLE_OF_ESTIMATE", 3.0) or 0)
)

_TERMINAL_JOB_STATUSES = ("delivered", "cancelled", "expired")


# ── Pure helpers (unit-tested without a database) ────────────────────────────

def money(value) -> Decimal:
    """Quantize any money-ish value to kobo the way the money stack does."""
    return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def bid_window_deadline(now: Optional[datetime] = None) -> datetime:
    """When a job created at `now` stops accepting new bids."""
    return (now or datetime.utcnow()) + timedelta(hours=BID_WINDOW_HOURS)


def is_bidding_open(job: DeliveryJob, now: Optional[datetime] = None) -> bool:
    """True while a bid job can still receive bids.

    A `None` deadline is treated as open (rows created before this feature or by
    an admin tool) instead of silently closing every legacy job.
    """
    if job is None or job.assignment_type != "bid":
        return False
    if (job.status or "") != "available":
        return False
    if job.bidding_closes_at is None:
        return True
    return job.bidding_closes_at > (now or datetime.utcnow())


def validate_delivery_fee(amount, estimate: Optional[float] = None) -> Decimal:
    """Validate a delivery amount and return it quantized.

    Raises ValueError with a caller-friendly message. Used for both vendor quotes
    and driver bids so a bad amount can never reach the buyer's invoice.
    """
    try:
        value = money(amount)
    except Exception:
        raise ValueError("Enter a valid delivery amount.")
    if value < 0:
        raise ValueError("Delivery fee cannot be negative.")
    if value > MAX_DELIVERY_FEE:
        raise ValueError(f"Delivery fee cannot exceed ₦{float(MAX_DELIVERY_FEE):,.2f}.")
    if estimate is not None and QUOTE_ESTIMATE_MULTIPLIER > 0:
        ceiling = money(Decimal(str(estimate)) * QUOTE_ESTIMATE_MULTIPLIER)
        if ceiling > 0 and value > ceiling:
            raise ValueError(
                "Delivery fee looks too high for this distance - contact support if this is correct."
            )
    return value


def unpaid_delivery_total(jobs: Sequence[DeliveryJob]) -> Decimal:
    """Sum of delivery fees still owed for the given jobs (invoice amount)."""
    total = Decimal("0.00")
    for job in jobs or []:
        if getattr(job, "shipping_paid", True):
            continue
        if (job.status or "") in ("cancelled", "expired"):
            continue
        total += money(job.delivery_fee)
    return money(total)


def _job_number() -> str:
    rnd = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"DLV-{datetime.utcnow().strftime('%Y%m%d')}-{rnd}"


def _notify(db: AsyncSession, user_id, title: str, message: str, link: str | None = None) -> None:
    if user_id is None:
        return
    db.add(Notification(user_id=user_id, type="delivery", title=title, message=message,
                        read=False, link=link))


def _write_event(db: AsyncSession, job: DeliveryJob, event_type: str, note: str,
                 actor_type: str = "system", actor_name: Optional[str] = None) -> None:
    db.add(DeliveryJobEvent(
        delivery_job_id=job.id,
        order_id=job.order_id,
        actor_type=actor_type,
        actor_name=actor_name,
        event_type=event_type,
        status=job.status,
        note=note,
    ))


# ── Job creation (called from the payment confirmation funnel) ───────────────

async def _buyer_id_for_order(db: AsyncSession, order_id) -> Optional[object]:
    return (await db.execute(select(Order.user_id).where(Order.id == order_id))).scalar_one_or_none()


async def _pickup_snapshot(db: AsyncSession, vendor_id):
    """Pickup address/coords for a vendor: base fields + best saved pin."""
    vendor = (await db.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one_or_none()
    city = vendor.city if vendor else ""
    state = vendor.state if vendor else ""
    address = (vendor.business_address or "") if vendor else ""
    lat = lng = None
    vaddr = (await db.execute(
        select(VendorAddress)
        .where(VendorAddress.vendor_id == vendor_id, VendorAddress.latitude.isnot(None))
        .order_by(VendorAddress.is_primary.desc(), VendorAddress.created_at.desc())
    )).scalars().first()
    if vaddr:
        lat = Decimal(str(vaddr.latitude)) if vaddr.latitude is not None else None
        lng = Decimal(str(vaddr.longitude)) if vaddr.longitude is not None else None
        if not address:
            address = vaddr.address_line1 or ""
        if not city:
            city = vaddr.city or ""
        if not state:
            state = vaddr.state or ""
    return vendor, address, city, state, lat, lng


async def _dropoff_snapshot(db: AsyncSession, order: Order):
    """Dropoff from the buyer's shipping address.

    Queried explicitly (not via `order.shipping_address`) so the helper is safe
    inside webhook handlers that load the order without eager loading.
    """
    if not order.shipping_address_id:
        return "", "", "", None, None
    addr = (await db.execute(
        select(CustomerAddress).where(CustomerAddress.id == order.shipping_address_id)
    )).scalar_one_or_none()
    if not addr:
        return "", "", "", None, None
    return (
        addr.address_line1 or "",
        addr.city or "",
        addr.state or "",
        Decimal(str(addr.latitude)) if addr.latitude is not None else None,
        Decimal(str(addr.longitude)) if addr.longitude is not None else None,
    )


async def create_bid_jobs_for_order(
    db: AsyncSession, order: Order, *, window_hours: Optional[int] = None
) -> List[DeliveryJob]:
    """Open one `bid` delivery job per vendor group for a newly paid order.

    Idempotent: a vendor group that already has a non-cancelled job (vendor
    dispatched first, or a retried webhook) is skipped. Does not commit - the
    caller owns the transaction.
    """
    items = (await db.execute(
        select(OrderItem).where(OrderItem.order_id == order.id)
    )).scalars().all()
    if not items:
        return []

    seen = set()
    vendor_ids = []
    for item in items:
        key = str(item.vendor_id)
        if key not in seen:
            seen.add(key)
            vendor_ids.append(item.vendor_id)

    dropoff_address, dropoff_city, dropoff_state, dropoff_lat, dropoff_lng = await _dropoff_snapshot(db, order)
    hours = int(window_hours or BID_WINDOW_HOURS)
    closes_at = datetime.utcnow() + timedelta(hours=hours)

    created: List[DeliveryJob] = []
    for vendor_id in vendor_ids:
        existing = (await db.execute(
            select(DeliveryJob.id).where(
                DeliveryJob.order_id == order.id,
                DeliveryJob.vendor_id == vendor_id,
                DeliveryJob.status.notin_(list(_TERMINAL_JOB_STATUSES)),
            )
        )).first()
        if existing:
            continue

        vendor, pickup_address, pickup_city, pickup_state, pickup_lat, pickup_lng = \
            await _pickup_snapshot(db, vendor_id)

        job = DeliveryJob(
            job_number=_job_number(),
            order_id=order.id,
            vendor_id=vendor_id,
            assignment_type="bid",
            status="available",
            pickup_address=pickup_address,
            pickup_city=pickup_city,
            pickup_state=pickup_state,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            dropoff_address=dropoff_address,
            dropoff_city=dropoff_city,
            dropoff_state=dropoff_state,
            dropoff_lat=dropoff_lat,
            dropoff_lng=dropoff_lng,
            delivery_fee=Decimal("0.00"),
            shipping_paid=False,
            bidding_closes_at=closes_at,
        )
        db.add(job)
        created.append(job)

    if created:
        await db.flush()
        for job in created:
            vendor = (await db.execute(
                select(Vendor).where(Vendor.id == job.vendor_id)
            )).scalar_one_or_none()
            _write_event(
                db, job, "bidding_opened",
                f"Delivery options opened for order {order.order_number}. "
                f"Bids close in {hours}h.", actor_type="system",
            )
            if vendor is not None:
                _notify(
                    db, vendor.user_id, "Arrange delivery",
                    f"Order {order.order_number}: choose your own driver or let nearby "
                    f"drivers bid. Bidding closes in {hours}h.",
                    "/dashboard/deliveries",
                )
        logger.info("Opened %s bid delivery job(s) for order %s", len(created), order.order_number)

    return created


# ── Driver bids ─────────────────────────────────────────────────────────────

async def place_bid(
    db: AsyncSession,
    job: DeliveryJob,
    driver: DriverProfile,
    amount,
    note: Optional[str] = None,
) -> DeliveryBid:
    """Create or replace a driver's bid on a job. Does not commit.

    Re-bidding updates the pending amount instead of stacking a second row, and a
    previously rejected/withdrawn bid may be re-opened while the window is open.
    """
    if job.assignment_type != "bid":
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            "This delivery is not open for bids.")
    if not is_bidding_open(job):
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "Bidding for this delivery has closed.")
    try:
        value = validate_delivery_fee(amount)
    except ValueError as exc:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, str(exc))
    if value <= 0:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            "Enter how much you want for this delivery.")

    bid = (await db.execute(
        select(DeliveryBid).where(
            DeliveryBid.job_id == job.id,
            DeliveryBid.driver_profile_id == driver.id,
        )
    )).scalar_one_or_none()

    first_bid_on_job = False
    if bid is None:
        bid = DeliveryBid(
            job_id=job.id,
            driver_profile_id=driver.id,
            amount=value,
            note=(note or "").strip() or None,
        )
        db.add(bid)
        first_bid_on_job = True
    elif bid.status == BID_ACCEPTED:
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "This bid was already accepted.")
    else:
        if bid.status != BID_PENDING:
            first_bid_on_job = True  # re-opened after withdrawal/rejection
        bid.amount = value
        bid.note = (note or "").strip() or None
        bid.status = BID_PENDING
        bid.decided_at = None

    await db.flush()

    buyer_id = await _buyer_id_for_order(db, job.order_id)
    order_number = (await db.execute(
        select(Order.order_number).where(Order.id == job.order_id)
    )).scalar_one_or_none()
    if first_bid_on_job:
        _notify(
            db, buyer_id, "New delivery bid",
            f"A driver offered ₦{float(value):,.2f} for order {order_number}'s delivery. "
            f"Review the bids before they close.",
            "/dashboard/orders",
        )
    logger.info("Driver %s bid ₦%s on job %s", driver.id, value, job.job_number)
    return bid


async def withdraw_bid(db: AsyncSession, bid: DeliveryBid, driver_id) -> DeliveryBid:
    """Withdraw the driver's own pending bid."""
    if str(bid.driver_profile_id) != str(driver_id):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "This bid is not yours.")
    if bid.status != BID_PENDING:
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "Only a pending bid can be withdrawn.")
    bid.status = BID_WITHDRAWN
    bid.decided_at = datetime.utcnow()
    await db.flush()
    return bid


async def reject_bid(db: AsyncSession, bid: DeliveryBid, buyer_id) -> DeliveryBid:
    """Buyer dismisses one bid without choosing it."""
    order_user = (await db.execute(
        select(Order.user_id)
        .join(DeliveryJob, DeliveryJob.order_id == Order.id)
        .where(DeliveryJob.id == bid.job_id)
    )).scalar_one_or_none()
    if order_user is None or str(order_user) != str(buyer_id):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "This delivery is not on your order.")
    if bid.status != BID_PENDING:
        raise HTTPException(http_status.HTTP_409_CONFLICT, "This bid is no longer pending.")
    bid.status = BID_REJECTED
    bid.decided_at = datetime.utcnow()
    await db.flush()
    return bid


async def accept_bid(db: AsyncSession, bid: DeliveryBid, buyer_id) -> tuple:
    """Accept one bid: atomically assign the driver and lock the fee. No commit."""
    job = (await db.execute(
        select(DeliveryJob).where(DeliveryJob.id == bid.job_id)
    )).scalar_one_or_none()
    if job is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Delivery job not found.")
    order = (await db.execute(select(Order).where(Order.id == job.order_id))).scalar_one_or_none()
    if order is None or str(order.user_id) != str(buyer_id):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "This delivery is not on your order.")
    if bid.status != BID_PENDING:
        raise HTTPException(http_status.HTTP_409_CONFLICT, "This bid is no longer pending.")
    if job.status != "available":
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "A driver has already been chosen for this delivery.")

    driver = (await db.execute(
        select(DriverProfile).where(DriverProfile.id == bid.driver_profile_id)
    )).scalar_one_or_none()
    if driver is None or driver.status != "active":
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "This driver is no longer available.")

    fee = money(bid.amount)
    claimed = await db.execute(
        update(DeliveryJob)
        .where(DeliveryJob.id == job.id, DeliveryJob.status == "available")
        .values(
            status="accepted",
            driver_profile_id=driver.id,
            driver_name=driver.full_name or driver.phone or "",
            driver_phone=driver.phone or "",
            delivery_fee=fee,
            accepted_at=datetime.utcnow(),
            shipping_paid=False,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "Another bid was accepted first. Refresh and try again.")

    bid.status = BID_ACCEPTED
    bid.decided_at = datetime.utcnow()

    # Every other pending bid on this group loses.
    await db.execute(
        update(DeliveryBid)
        .where(
            DeliveryBid.job_id == job.id,
            DeliveryBid.id != bid.id,
            DeliveryBid.status == BID_PENDING,
        )
        .values(status=BID_REJECTED, decided_at=datetime.utcnow())
        .execution_options(synchronize_session=False)
    )

    order.driver_name = driver.full_name or driver.phone or ""
    order.driver_phone = driver.phone or ""
    order.shipping_payment_status = "pending"

    await db.flush()
    await db.refresh(job)

    vendor = (await db.execute(select(Vendor).where(Vendor.id == job.vendor_id))).scalar_one_or_none()
    _write_event(db, job, "bid_accepted",
                 f"Buyer accepted a ₦{float(fee):,.2f} driver bid. Delivery payment pending.",
                 actor_type="buyer")
    _notify(db, driver.user_id, "Your delivery bid was accepted",
            f"Order {order.order_number}: your ₦{float(fee):,.2f} bid won. "
            f"Wait for the buyer's delivery payment before pickup.",
            "/driver/dashboard")
    if vendor is not None:
        _notify(db, vendor.user_id, "Driver selected",
                f"Order {order.order_number}: the buyer chose a driver at "
                f"₦{float(fee):,.2f}. Delivery payment is pending.",
                "/dashboard/deliveries")
    return job, bid


# ── Vendor quote / buyer fallbacks ──────────────────────────────────────────

async def apply_vendor_quote(db: AsyncSession, job: DeliveryJob, amount) -> DeliveryJob:
    """Vendor offers to deliver the group themselves for `amount`.

    Closes bidding (existing pending bids are expired) and shows the buyer a
    pay-to-accept option alongside the "wait for bids" fallback. Does not commit.
    """
    if job.status != "available":
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "This delivery has already been arranged.")
    if getattr(job, "shipping_paid", False):
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "Delivery for this order is already paid.")
    try:
        fee = validate_delivery_fee(amount)
    except ValueError as exc:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, str(exc))
    if fee <= 0:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            "Enter the delivery fee you want to charge.")

    job.delivery_fee = fee
    job.bidding_closes_at = datetime.utcnow()
    await db.execute(
        update(DeliveryBid)
        .where(DeliveryBid.job_id == job.id, DeliveryBid.status == BID_PENDING)
        .values(status=BID_EXPIRED, decided_at=datetime.utcnow())
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    order = (await db.execute(select(Order).where(Order.id == job.order_id))).scalar_one_or_none()
    _write_event(db, job, "vendor_quote",
                 f"Vendor quoted ₦{float(fee):,.2f} to deliver this group.",
                 actor_type="vendor")
    if order is not None:
        _notify(
            db, order.user_id, "Vendor delivery offer",
            f"Order {order.order_number}: the vendor can deliver for ₦{float(fee):,.2f}. "
            f"Pay the delivery fee to accept, or choose a driver bid instead.",
            "/dashboard/orders",
        )
    return job


async def decline_vendor_delivery(db: AsyncSession, job: DeliveryJob, buyer_id) -> List[DeliveryJob]:
    """Buyer rejects a vendor quote; a fresh bid job is opened for that vendor."""
    order = (await db.execute(select(Order).where(Order.id == job.order_id))).scalar_one_or_none()
    if order is None or str(order.user_id) != str(buyer_id):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "This delivery is not on your order.")
    if getattr(job, "shipping_paid", False):
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "Delivery for this order is already paid.")
    if job.status in _TERMINAL_JOB_STATUSES:
        raise HTTPException(http_status.HTTP_409_CONFLICT, "This delivery is no longer active.")
    if job.picked_up_at is not None:
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "The driver has already picked up this order.")

    job.vendor_fee_declined_at = datetime.utcnow()
    job.status = "cancelled"
    job.cancelled_at = job.vendor_fee_declined_at
    await db.execute(
        update(DeliveryBid)
        .where(DeliveryBid.job_id == job.id, DeliveryBid.status == BID_PENDING)
        .values(status=BID_WITHDRAWN, decided_at=datetime.utcnow())
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    vendor = (await db.execute(select(Vendor).where(Vendor.id == job.vendor_id))).scalar_one_or_none()
    _write_event(db, job, "quote_declined",
                 "Buyer declined the vendor's delivery fee; reopened driver bidding.",
                 actor_type="buyer")
    if vendor is not None:
        _notify(db, vendor.user_id, "Vendor delivery declined",
                f"Order {order.order_number}: the buyer declined your delivery fee. "
                f"Nearby drivers can bid again.",
                "/dashboard/deliveries")

    # Reuse the standard creator: the cancelled job no longer blocks a new one.
    return await create_bid_jobs_for_order(db, order)


async def extend_bidding(db: AsyncSession, job: DeliveryJob, buyer_id=None) -> DeliveryJob:
    """Push the bidding deadline out by EXTENSION_HOURS (buyer asks for more bids)."""
    if buyer_id is not None:
        owner = await _buyer_id_for_order(db, job.order_id)
        if owner is None or str(owner) != str(buyer_id):
            raise HTTPException(http_status.HTTP_403_FORBIDDEN, "This delivery is not on your order.")
    if job.assignment_type != "bid" or job.status != "available":
        raise HTTPException(http_status.HTTP_409_CONFLICT, "This delivery is not open for bids.")
    job.bidding_closes_at = datetime.utcnow() + timedelta(hours=EXTENSION_HOURS)
    await db.flush()
    _write_event(db, job, "bidding_extended",
                 f"Bidding extended by {EXTENSION_HOURS}h.", actor_type="buyer")
    return job


