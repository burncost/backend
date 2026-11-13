"""Post-confirmation delivery arrangement API (bidding + separate shipping payment).

Mounted under `/delivery` next to the dispatch engine (`deliveries.py`).

Buyer
-----
- `GET  /delivery/orders/{order_ref}/arrangement`  per-vendor jobs, bids, quotes, amount due
- `POST /delivery/bids/{bid_id}/accept`            choose a driver (locks the fee)
- `POST /delivery/bids/{bid_id}/reject`            dismiss one bid
- `POST /delivery/jobs/{job_id}/decline-vendor`    refuse the vendor quote, reopen bidding
- `POST /delivery/jobs/{job_id}/extend`            add EXTENSION_HOURS to the window
- `POST /delivery/orders/{order_ref}/shipping/pay` create the delivery invoice (gateway link)
- `POST /delivery/shipping/confirm`                mock-mode settlement
- `GET  /delivery/shipping/verify`                 gateway return-trip settlement

Driver
------
- `GET    /delivery/bids/open`     bid jobs in the driver's area (+ own bid)
- `POST   /delivery/jobs/{job_id}/bids`
- `DELETE /delivery/bids/{bid_id}` withdraw
- `GET    /delivery/bids/mine`

Vendor
------
- `POST /delivery/jobs/{job_id}/quote`   vendor delivers it themselves at a fee
- `POST /delivery/jobs/{job_id}/assign`  give the job to one of their fleet drivers at a fee

Money never comes from the client: `build_invoice` recomputes the amount from the
unpaid jobs and `confirm_reference` only releases the jobs it snapshotted.
"""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_driver_profile, get_current_user, get_current_verified_vendor
from app.api.v1.endpoints.deliveries import (
    _job_dict,
    _load_order,
    _notify,
    _rank_offers,
    _require_vehicle_photos,
)
from app.core.database import get_db
from app.models.delivery import DeliveryJob
from app.models.delivery_bid import BID_ACCEPTED, BID_EXPIRED, BID_PENDING, DeliveryBid
from app.models.delivery_event import DeliveryJobEvent
from app.models.driver import DriverProfile
from app.models.order import Order
from app.models.shipping_invoice import INVOICE_COMPLETED, ShippingInvoice
from app.models.vendor import Vendor
from app.services import shipping_payment_service as shipping
from app.services.bid_dispatch_service import (
    accept_bid,
    apply_vendor_quote,
    decline_vendor_delivery,
    extend_bidding,
    is_bidding_open,
    money,
    place_bid,
    reject_bid,
    unpaid_delivery_total,
    validate_delivery_fee,
    withdraw_bid,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_ADMIN_ROLES = ("admin", "super_admin")

# Buyer-facing outcome copy for a delivery settlement. Kept next to the routes so
# every client (card, return trip, polling) reports the same thing for the same
# `confirm_reference` status.
_SETTLEMENT_MESSAGES = {
    "settled": "Delivery payment confirmed. The driver can now pick up your goods.",
    "already_settled": "This delivery payment was already confirmed.",
    "already_covered": "These delivery jobs were already paid on another link - this charge is flagged for refund.",
    "amount_mismatch": "The amount paid does not match this delivery quote. Nothing was released.",
    "stale": "The delivery fee changed after this link was created. Please pay the new amount.",
    "not_found": "Delivery payment not found.",
    "forbidden": "This delivery payment belongs to another buyer.",
}



# ── Request shapes ────────────────────────────────────────────────────────────
class BidCreate(BaseModel):
    amount: float = Field(..., gt=0)
    note: Optional[str] = Field(None, max_length=500)


class QuoteCreate(BaseModel):
    amount: float = Field(..., gt=0)


class AssignCreate(BaseModel):
    driver_profile_id: UUID
    amount: float = Field(..., gt=0)


class ShippingConfirm(BaseModel):
    reference: str = Field(..., min_length=4, max_length=100)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _role_of(user) -> str:
    role = getattr(user, "role", None)
    return role.value if hasattr(role, "value") else str(role or "")


async def _buyer_order(db: AsyncSession, order_ref: str, user) -> Order:
    """Load an order the caller owns (admins may inspect any order)."""
    order = await _load_order(db, order_ref)
    if _role_of(user) not in _ADMIN_ROLES and str(order.user_id) != str(user.id):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="This order is not yours.")
    return order


def _bid_dict(bid: DeliveryBid, driver: DriverProfile) -> dict:
    return {
        "id": str(bid.id),
        "driver_profile_id": str(bid.driver_profile_id),
        "driver_name": driver.full_name or driver.phone or "Driver",
        # Contact details are only revealed once the buyer has chosen this driver.
        "driver_phone": driver.phone if bid.status == BID_ACCEPTED else None,
        "driver_rating": float(driver.rating) if driver.rating is not None else None,
        "driver_rating_count": int(driver.rating_count or 0),
        "vehicle_type": driver.vehicle_type,
        "amount": float(bid.amount or 0),
        "note": bid.note,
        "status": bid.status,
        "created_at": bid.created_at,
        "decided_at": bid.decided_at,
    }


async def _bids_for_jobs(db: AsyncSession, job_ids) -> dict:
    """{job_id: [(bid, driver), ...]} for the pending/accepted bids of these jobs."""
    if not job_ids:
        return {}
    rows = (await db.execute(
        select(DeliveryBid, DriverProfile)
        .join(DriverProfile, DriverProfile.id == DeliveryBid.driver_profile_id)
        .where(
            DeliveryBid.job_id.in_(job_ids),
            DeliveryBid.status.in_([BID_PENDING, BID_ACCEPTED]),
        )
        .order_by(DeliveryBid.amount.asc(), DeliveryBid.created_at.asc())
    )).all()
    grouped = {}
    for bid, driver in rows:
        grouped.setdefault(bid.job_id, []).append((bid, driver))
    return grouped


async def _load_job(db: AsyncSession, job_id: UUID) -> DeliveryJob:
    job = (await db.execute(
        select(DeliveryJob).options(selectinload(DeliveryJob.order)).where(DeliveryJob.id == job_id)
    )).scalar_one_or_none()
    if job is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Delivery job not found.")
    return job


async def _vendor_for_user(db: AsyncSession, current_vendor: dict) -> Vendor:
    vendor = (await db.execute(
        select(Vendor).where(Vendor.id == UUID(current_vendor["id"]))
    )).scalar_one_or_none()
    if vendor is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Vendor profile not found.")
    return vendor


def _require_vendor_job(job: DeliveryJob, vendor: Vendor) -> None:
    if str(job.vendor_id) != str(vendor.id):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN,
                            detail="This delivery is not for your catalog.")


def _event(db: AsyncSession, job: DeliveryJob, event_type: str, note: str, actor_type: str) -> None:
    db.add(DeliveryJobEvent(
        delivery_job_id=job.id,
        order_id=job.order_id,
        actor_type=actor_type,
        event_type=event_type,
        status=job.status,
        note=note,
    ))


# ── Buyer: arrangement overview ───────────────────────────────────────────────
@router.get("/orders/{order_ref}/arrangement")
async def buyer_delivery_arrangement(
    order_ref: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Everything the buyer needs to arrange delivery for one order."""
    order = await _buyer_order(db, order_ref, current_user)

    jobs = list((await db.execute(
        select(DeliveryJob)
        .where(DeliveryJob.order_id == order.id)
        .order_by(DeliveryJob.created_at.asc())
    )).scalars().all())

    grouped = await _bids_for_jobs(db, [j.id for j in jobs])
    pending_invoice = await shipping.pending_invoice(db, order.id)

    payload_jobs = []
    for job in jobs:
        item = _job_dict(job, order_number=order.order_number)
        fee = money(job.delivery_fee)
        item.update({
            "bidding_open": is_bidding_open(job),
            "bids": [_bid_dict(b, d) for b, d in grouped.get(job.id, [])],
            # A vendor quote exists while nobody is assigned and a fee is set.
            "vendor_quote": (
                float(fee) if job.assignment_type == "bid"
                and job.driver_profile_id is None and fee > 0 else None
            ),
            "amount_due": 0.0 if job.shipping_paid else float(fee),
        })
        payload_jobs.append(item)

    return {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "shipping_payment_status": order.shipping_payment_status,
        "shipping_paid_at": order.shipping_paid_at,
        "delivery_fee_total": float(order.delivery_fee_total or 0),
        "amount_due": float(unpaid_delivery_total(jobs)),
        "pending_invoice_reference": pending_invoice.reference if pending_invoice else None,
        "jobs": payload_jobs,
    }


# ── Buyer: bid decisions ──────────────────────────────────────────────────────
@router.post("/bids/{bid_id}/accept")
async def buyer_accept_bid(
    bid_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    bid = (await db.execute(select(DeliveryBid).where(DeliveryBid.id == bid_id))).scalar_one_or_none()
    if bid is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Bid not found.")

    job, _chosen = await accept_bid(db, bid, current_user.id)
    order = (await db.execute(select(Order).where(Order.id == job.order_id))).scalar_one()
    order_id, order_number = order.id, order.order_number
    fee = float(job.delivery_fee or 0)
    await db.commit()
    await db.refresh(job)

    return {
        "success": True,
        "job": _job_dict(job, order_number=order_number),
        "delivery_fee": fee,
        "amount_due": float(await shipping.delivery_due(db, order_id)),
        "message": "Driver chosen. Pay the delivery fee so they can pick up the goods.",
    }


@router.post("/bids/{bid_id}/reject")
async def buyer_reject_bid(
    bid_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    bid = (await db.execute(select(DeliveryBid).where(DeliveryBid.id == bid_id))).scalar_one_or_none()
    if bid is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Bid not found.")

    bid = await reject_bid(db, bid, current_user.id)
    bid_id_value, status_value = str(bid.id), bid.status
    await db.commit()
    return {"success": True, "bid_id": bid_id_value, "status": status_value}


@router.post("/jobs/{job_id}/decline-vendor")
async def buyer_decline_vendor_quote(
    job_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _load_job(db, job_id)
    order = (await db.execute(select(Order).where(Order.id == job.order_id))).scalar_one_or_none()
    reopened = await decline_vendor_delivery(db, job, current_user.id)
    order_number = order.order_number if order else None
    reopened_payload = [_job_dict(j, order_number=order_number) for j in reopened]
    await db.commit()
    return {
        "success": True,
        "order_number": order_number,
        "reopened_jobs": reopened_payload,
    }


@router.post("/jobs/{job_id}/extend")
async def buyer_extend_bidding(
    job_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _load_job(db, job_id)
    order_number = job.order.order_number if job.order else None
    job = await extend_bidding(db, job, buyer_id=current_user.id)
    payload = _job_dict(job, order_number=order_number)
    await db.commit()
    return payload


# ── Buyer: separate delivery payment ─────────────────────────────────────────
@router.post("/orders/{order_ref}/shipping/pay")
async def create_shipping_payment(
    order_ref: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start the delivery payment (amount is always server-computed)."""
    order = await _buyer_order(db, order_ref, current_user)
    result = await shipping.build_invoice(db, order)
    if result.get("success"):
        return result

    code = result.get("error")
    if code == "order_unpaid":
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=result.get("message"))
    if code == "nothing_due":
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=result.get("message"))
    raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, detail=result.get("message"))


@router.post("/shipping/confirm")
async def confirm_shipping_payment(
    payload: ShippingConfirm,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Settle a delivery invoice without a gateway (mock/dev mode only)."""
    invoice = (await db.execute(
        select(ShippingInvoice).where(ShippingInvoice.reference == payload.reference)
    )).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Delivery payment not found.")
    if (invoice.provider or "") not in ("", "mock"):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            detail="This delivery payment must be confirmed by the gateway.")
    if invoice.status == INVOICE_COMPLETED:
        return {"status": "already_settled", "reference": payload.reference,
                "amount": float(invoice.amount or 0)}

    result = await shipping.confirm_reference(
        db, payload.reference, provider="mock", user_id=str(current_user.id),
    )
    if result.get("status") not in ("settled", "already_settled"):
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=result.get("status"))
    return result


@router.get("/shipping/verify")
async def verify_shipping_payment(
    reference: str,
    provider: Optional[str] = None,
    gateway: Optional[str] = None,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Gateway return trip: verify the charge, then settle the delivery invoice.

    The buyer lands back on `/dashboard/orders?shipping_ref=SHP-...`; the client
    calls this with the reference the gateway echoed (query params carry it as
    `reference`/`trxref`/`paymentReference`, so both names are accepted here).

    Bounded on purpose: only the invoice owner (or an admin) can trigger a
    settlement, and the gateway that created the invoice is preferred over the
    one the client names, so a redirect can never settle somebody else's delivery
    or query a different gateway than the one that took the money. Settlement is
    idempotent by reference, so a webhook that already landed simply wins the
    race and this reports `already_settled`.
    """
    ref = (reference or "").strip()
    invoice = (await db.execute(
        select(ShippingInvoice).where(ShippingInvoice.reference == ref)
    )).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Delivery payment not found.")

    order = (await db.execute(select(Order).where(Order.id == invoice.order_id))).scalar_one_or_none()
    if order is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Delivery payment not found.")
    if _role_of(current_user) not in _ADMIN_ROLES and str(order.user_id) != str(current_user.id):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN,
                            detail="This delivery payment belongs to another buyer.")

    # A webhook (or an earlier return trip) may already have settled it.
    if invoice.status == INVOICE_COMPLETED:
        return {
            "success": True,
            "applied": True,
            "reference": ref,
            "amount": float(invoice.amount or 0),
            "shipping": {"status": "already_settled", "reference": ref,
                         "amount": float(invoice.amount or 0)},
            "message": _SETTLEMENT_MESSAGES["already_settled"],
        }

    invoice_provider = (invoice.provider or "").strip().lower()
    requested = (provider or gateway or "").strip().lower()
    chosen = invoice_provider or requested or "flutterwave"

    # Mock/dev invoices never touched a gateway - settle locally.
    if chosen == "mock":
        settlement = await shipping.confirm_reference(
            db, ref, provider="mock", user_id=str(current_user.id))
        applied = settlement.get("status") in ("settled", "already_settled")
        return {
            "success": applied,
            "applied": applied,
            "reference": ref,
            "provider": "mock",
            "shipping": settlement,
            "message": _SETTLEMENT_MESSAGES.get(settlement.get("status"),
                                                "Delivery payment recorded."),
        }

    from app.api.v1.endpoints.payments import _gateway_amount
    from app.services.payment_service import PaymentService

    verified = await PaymentService().verify_payment(ref, provider=chosen)
    if not verified.get("success"):
        return {
            **verified,
            "applied": False,
            "message": verified.get("error") or "We could not confirm that delivery payment yet.",
        }

    gateway_name = str(verified.get("provider") or chosen)
    is_mock = gateway_name.lower() == "mock"
    settlement = await shipping.confirm_reference(
        db,
        ref,
        provider=gateway_name,
        paid_amount=None if is_mock else _gateway_amount(verified.get("amount")),
        provider_reference=str(verified.get("id") or ref),
        user_id=str(current_user.id),
    )
    applied = settlement.get("status") in ("settled", "already_settled")
    return {
        **verified,
        "reference": ref,
        "applied": applied,
        "shipping": settlement,
        "message": _SETTLEMENT_MESSAGES.get(settlement.get("status"),
                                            "Delivery payment recorded."),
    }


# ── Vendor: quote or assign a fleet driver ────────────────────────────────────
@router.post("/jobs/{job_id}/quote")
async def vendor_quote_delivery(
    job_id: UUID,
    payload: QuoteCreate,
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    job = await _load_job(db, job_id)
    vendor = await _vendor_for_user(db, current_vendor)
    _require_vendor_job(job, vendor)

    job = await apply_vendor_quote(db, job, payload.amount)
    order_number = job.order.order_number if job.order else None
    await db.commit()
    await db.refresh(job)
    return _job_dict(job, order_number=order_number)


@router.post("/jobs/{job_id}/assign")
async def vendor_assign_delivery(
    job_id: UUID,
    payload: AssignCreate,
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    """Vendor delivers it themselves: one of their fleet drivers at a quoted fee."""
    job = await _load_job(db, job_id)
    vendor = await _vendor_for_user(db, current_vendor)
    _require_vendor_job(job, vendor)

    if job.assignment_type != "bid":
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            detail="This delivery was already arranged outside bidding.")
    if job.status != "available" or job.shipping_paid:
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            detail="This delivery has already been arranged.")

    try:
        fee = validate_delivery_fee(payload.amount)
    except ValueError as exc:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if fee <= 0:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            detail="Enter the delivery fee you want to charge.")

    driver = (await db.execute(
        select(DriverProfile).where(
            DriverProfile.id == payload.driver_profile_id,
            DriverProfile.vendor_id == vendor.id,
            DriverProfile.status == "active",
        )
    )).scalar_one_or_none()
    if driver is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                            detail="Driver not found in your fleet or not active.")

    now = datetime.utcnow()
    claimed = await db.execute(
        update(DeliveryJob)
        .where(DeliveryJob.id == job.id, DeliveryJob.status == "available")
        .values(
            status="accepted",
            driver_profile_id=driver.id,
            driver_name=driver.full_name or driver.phone or "",
            driver_phone=driver.phone or "",
            delivery_fee=fee,
            accepted_at=now,
            shipping_paid=False,
            bidding_closes_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            detail="This delivery was arranged a moment ago. Refresh and try again.")

    # Driver bids that were still pending lose the job.
    await db.execute(
        update(DeliveryBid)
        .where(DeliveryBid.job_id == job.id, DeliveryBid.status == BID_PENDING)
        .values(status=BID_EXPIRED, decided_at=now)
        .execution_options(synchronize_session=False)
    )

    order = (await db.execute(select(Order).where(Order.id == job.order_id))).scalar_one_or_none()
    if order is not None:
        order.driver_name = driver.full_name or driver.phone or ""
        order.driver_phone = driver.phone or ""
        order.shipping_payment_status = "pending"
        _notify(db, order.user_id, "Delivery fee to pay",
                f"Order {order.order_number}: your vendor arranged delivery for "
                f"₦{float(fee):,.2f}. Pay it from the order page.",
                "/dashboard/orders")
    _notify(db, driver.user_id, "New delivery assignment",
            f"You have been assigned delivery {job.job_number} at ₦{float(fee):,.2f}.",
            "/driver/dashboard")
    _event(db, job, "vendor_assigned",
           f"Vendor assigned a fleet driver at ₦{float(fee):,.2f}. Delivery payment pending.",
           "vendor")

    order_number = order.order_number if order else None
    await db.commit()
    await db.refresh(job)
    return {
        **_job_dict(job, order_number=order_number),
        "amount_due": float(fee),
    }


# ── Driver: bid board ─────────────────────────────────────────────────────────
@router.get("/bids/open")
async def driver_open_bids(
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Open bid jobs near the driver, with their own bid already attached."""
    if not profile.availability or profile.status != "active" or not profile.vehicle_photos_complete:
        return []

    jobs = list((await db.execute(
        select(DeliveryJob)
        .options(selectinload(DeliveryJob.order))
        .where(
            DeliveryJob.assignment_type == "bid",
            DeliveryJob.status == "available",
            or_(
                DeliveryJob.bidding_closes_at.is_(None),
                DeliveryJob.bidding_closes_at > datetime.utcnow(),
            ),
        )
        .order_by(DeliveryJob.created_at.asc())
    )).scalars().all())
    jobs = _rank_offers(jobs, profile.default_state, profile.default_city)

    mine = {}
    counts = {}
    if jobs:
        rows = (await db.execute(
            select(DeliveryBid).where(
                DeliveryBid.job_id.in_([j.id for j in jobs]),
                DeliveryBid.status == BID_PENDING,
            )
        )).scalars().all()
        for bid in rows:
            counts[bid.job_id] = counts.get(bid.job_id, 0) + 1
            if str(bid.driver_profile_id) == str(profile.id):
                mine[bid.job_id] = bid

    payload = []
    for job in jobs:
        item = _job_dict(job, order_number=job.order.order_number if job.order else None)
        own = mine.get(job.id)
        item.update({
            "bid_count": counts.get(job.id, 0),
            "my_bid": None if own is None else {
                "id": str(own.id),
                "amount": float(own.amount or 0),
                "note": own.note,
                "status": own.status,
            },
        })
        payload.append(item)
    return payload


@router.get("/bids/mine")
async def driver_my_bids(
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(DeliveryBid, DeliveryJob)
        .join(DeliveryJob, DeliveryJob.id == DeliveryBid.job_id)
        .options(selectinload(DeliveryJob.order))
        .where(DeliveryBid.driver_profile_id == profile.id)
        .order_by(DeliveryBid.created_at.desc())
    )).all()
    return [
        {
            **_job_dict(job, order_number=job.order.order_number if job.order else None),
            "bid": {
                "id": str(bid.id),
                "amount": float(bid.amount or 0),
                "note": bid.note,
                "status": bid.status,
                "created_at": bid.created_at,
                "decided_at": bid.decided_at,
            },
        }
        for bid, job in rows
    ]


@router.post("/jobs/{job_id}/bids")
async def driver_place_bid(
    job_id: UUID,
    payload: BidCreate,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    if not profile.availability:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            detail="You are offline. Turn on availability to place a delivery bid.")
    if profile.status != "active":
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Driver account is not active.")
    _require_vehicle_photos(profile)

    job = await _load_job(db, job_id)
    bid = await place_bid(db, job, profile, payload.amount, note=payload.note)
    closes_at = job.bidding_closes_at
    await db.commit()
    await db.refresh(bid)
    return {
        "success": True,
        "bid": {
            "id": str(bid.id),
            "job_id": str(bid.job_id),
            "amount": float(bid.amount or 0),
            "note": bid.note,
            "status": bid.status,
        },
        "bidding_closes_at": closes_at,
    }


@router.delete("/bids/{bid_id}")
async def driver_withdraw_bid(
    bid_id: UUID,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    bid = (await db.execute(select(DeliveryBid).where(DeliveryBid.id == bid_id))).scalar_one_or_none()
    if bid is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Bid not found.")

    bid = await withdraw_bid(db, bid, profile.id)
    status_value = bid.status
    bid_id_value = str(bid.id)
    await db.commit()
    return {"success": True, "bid_id": bid_id_value, "status": status_value}


# Alias for clients that cannot send DELETE on this route.
@router.post("/bids/{bid_id}/withdraw")
async def driver_withdraw_bid_post(
    bid_id: UUID,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    return await driver_withdraw_bid(bid_id, profile=profile, db=db)
