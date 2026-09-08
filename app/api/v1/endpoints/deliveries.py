"""Delivery / dispatch engine (Phase 3).

Vendor side:
- list their fleet drivers
- create a DeliveryJob for one of their orders: `direct` (auto-accept a chosen
  driver) or `offer` (published to nearby available drivers)
- list / track their dispatches, cancel an open dispatch

Driver side:
- list open `offer` jobs near their default location (only while online)
- accept an offer atomically (only one driver can win)
- list their jobs, advance picked_up / delivered

Order sync: job state is mirrored onto the order (driver_name/phone + order
status transitions) so the existing buyer tracking / admin views keep working.
Notifications are written for vendor, driver and buyer at each step.
"""
import math
import random
import string
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_driver_profile, get_current_verified_vendor, get_current_user
from app.core.database import get_db
from app.models.driver import DriverProfile, DriverLocationUpdate
from app.models.delivery import DeliveryJob
from app.models.delivery_proof import DeliveryProof
from app.models.delivery_rating import DeliveryRating
from app.models.driver_earnings import DriverEarnings
from app.models.notification import Notification
from app.models.order import Order, OrderItem
from app.models.vendor import Vendor
from app.utils.storage import StorageService

router = APIRouter()

_ACTIVE_JOB_STATUSES = ("accepted", "picked_up", "in_transit")
_TERMINAL = ("delivered", "cancelled", "expired")

# Burncost commission taken on each independent-driver delivery payout (net of
# the fee before it reaches the driver). Kept at 0 by default so no driver is
# charged until this rate is deliberately configured; payout runs land in the
# Settlement & Disbursement phase.
_DRIVER_COMMISSION_RATE = Decimal("0.00")


# ── Request/response shapes ────────────────────────────────────────────
class DriverLiveLocation(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    accuracy: Optional[float] = Field(None, ge=0)
    job_id: Optional[UUID] = None


class DispatchCreate(BaseModel):
    order_id: str
    assignment_type: str = Field(..., pattern="^(direct|offer)$")
    driver_profile_id: Optional[UUID] = None
    pickup_lat: Optional[float] = Field(None, ge=-90, le=90)
    pickup_lng: Optional[float] = Field(None, ge=-180, le=180)
    # (customer may provide pickup coords at dispatch for live tracking)


class StatusAction(BaseModel):
    pass


class RatingCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1000)


class DriverLinkRequest(BaseModel):
    phone: str = Field(..., min_length=5, max_length=20)


def _norm_phone(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())[-10:]

def _haversine_km(lat1, lng1, lat2, lng2) -> Optional[float]:
    """Haversine distance in kilometres; None if any coordinate missing."""
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return None
    r = 6371.0
    p1, p2, l1, l2 = map(math.radians, (float(lat1), float(lat2), float(lng1), float(lng2)))
    dlat = p2 - p1
    dlng = l2 - l1
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return round(r * 2 * math.asin(math.sqrt(a)), 3)

class DriverInviteRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., min_length=5, max_length=20)
    email: Optional[str] = None

def _job_number() -> str:
    rnd = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"DLV-{datetime.utcnow().strftime('%Y%m%d')}-{rnd}"


def _job_dict(job: DeliveryJob, order_number: Optional[str] = None) -> dict:
    return {
        "id": str(job.id),
        "job_number": job.job_number,
        "order_id": str(job.order_id),
        "order_number": order_number or (job.order.order_number if job.order else None),
        "vendor_id": str(job.vendor_id),
        "driver_profile_id": str(job.driver_profile_id) if job.driver_profile_id else None,
        "assignment_type": job.assignment_type,
        "status": job.status,
        "pickup_city": job.pickup_city,
        "pickup_state": job.pickup_state,
        "pickup_address": job.pickup_address,
        "dropoff_city": job.dropoff_city,
        "dropoff_state": job.dropoff_state,
        "dropoff_address": job.dropoff_address,
        "driver_name": job.driver_name,
        "driver_phone": job.driver_phone,
        "delivery_fee": float(job.delivery_fee or 0),
        "accepted_at": job.accepted_at,
        "picked_up_at": job.picked_up_at,
        "delivered_at": job.delivered_at,
        "cancelled_at": job.cancelled_at,
        "created_at": job.created_at,
    }


def _notify(db: AsyncSession, user_id, title: str, message: str) -> None:
    db.add(Notification(user_id=user_id, type="delivery", title=title, message=message, read=False))


async def _load_order(db: AsyncSession, order_ref: str) -> Order:
    # Accept either an order UUID or its human-friendly order number.
    try:
        uid = UUID(order_ref)
    except ValueError:
        uid = None
    q = (
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.user),
            selectinload(Order.shipping_address),
        )
    )
    q = q.where(Order.id == uid) if uid is not None else q.where(Order.order_number == order_ref)
    result = await db.execute(q)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


async def _vendor_owns_order(db: AsyncSession, order: Order, vendor_id: UUID) -> bool:
    return any(str(item.vendor_id) == str(vendor_id) for item in order.items)


async def _set_order_driver(order: Order, driver: Optional[DriverProfile]) -> None:
    if driver:
        order.driver_name = driver.full_name or driver.phone or ""
        order.driver_phone = driver.phone or ""


def _advance_order_status(order: Order, target: str) -> None:
    if order.status is None or order.status.value.lower() in _TERMINAL:
        return
    if target == "shipped":
        if order.status.value.lower() in ("confirmed", "processing", "ready_for_pickup", "pending", "pending_payment"):
            order.status = "shipped"
    elif target == "delivered":
        order.status = "delivered"
        order.delivered_at = datetime.utcnow()
        order.payment_status = "completed"


async def _credit_driver_earnings(db: AsyncSession, job: DeliveryJob) -> None:
    """Accrue a pending earnings row for an independent (non-fleet) driver.

    Fleet drivers (`vendor_id` set) are paid by their vendor through the escrow
    flow, so they are intentionally excluded. Idempotent: one row per job.
    """
    if not job or not job.driver_profile_id:
        return
    drow = await db.execute(select(DriverProfile).where(DriverProfile.id == job.driver_profile_id))
    driver = drow.scalar_one_or_none()
    if not driver or driver.vendor_id is not None or driver.source != "self":
        return
    dup = await db.execute(select(DriverEarnings.id).where(DriverEarnings.delivery_job_id == job.id))
    if dup.first():
        return
    gross = Decimal(str(job.delivery_fee or 0))
    commission = (gross * _DRIVER_COMMISSION_RATE).quantize(Decimal("0.01"))
    db.add(DriverEarnings(
        driver_profile_id=job.driver_profile_id,
        delivery_job_id=job.id,
        gross_amount=gross,
        commission_amount=commission,
        net_amount=gross - commission,
        status="pending",
    ))


# ═══════════════════════════ VENDOR ENDPOINTS ══════════════════════════

@router.get("/drivers")
async def vendor_list_drivers(
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    """List the drivers currently linked to this vendor's fleet."""
    vendor_id = UUID(current_vendor["id"])
    result = await db.execute(
        select(DriverProfile)
        .where(DriverProfile.vendor_id == vendor_id)
        .order_by(DriverProfile.created_at.desc())
    )
    drivers = result.scalars().all()
    return [
        {
            "id": str(d.id),
            "full_name": d.full_name,
            "phone": d.phone,
            "status": d.status,
            "availability": d.availability,
            "vehicle_type": d.vehicle_type,
            "vehicle_plate": d.vehicle_plate,
            "default_city": d.default_city,
            "default_state": d.default_state,
        }
        for d in drivers
    ]


@router.get("/drivers/assignable")
async def vendor_assignable_drivers(
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    """List active drivers the vendor can direct-assign (fleet + independent)."""
    vendor_id = UUID(current_vendor["id"])
    result = await db.execute(
        select(DriverProfile)
        .where(DriverProfile.status == "active")
        .order_by(DriverProfile.created_at.desc())
    )
    drivers = result.scalars().all()
    return [
        {
            "id": str(d.id),
            "full_name": d.full_name,
            "phone": d.phone,
            "availability": d.availability,
            "vehicle_type": d.vehicle_type,
            "default_city": d.default_city,
            "default_state": d.default_state,
            "in_my_fleet": bool(d.vendor_id and str(d.vendor_id) == str(vendor_id)),
        }
        for d in drivers
    ]


@router.post("/drivers/link")
async def vendor_link_driver(
    payload: DriverLinkRequest,
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    """Add an existing active (self-onboarded) driver to this vendor fleet by phone."""
    vendor_id = UUID(current_vendor["id"])
    want = _norm_phone(payload.phone)
    result = await db.execute(select(DriverProfile).where(DriverProfile.status == "active"))
    drivers = result.scalars().all()
    match = None
    for d in drivers:
        if d.phone and _norm_phone(d.phone) == want:
            match = d
            break
    if not match:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            detail="No active driver found with that phone number. Ask them to create a driver account first.",
        )
    match.vendor_id = vendor_id
    match.source = "vendor"
    await db.commit()
    await db.refresh(match)
    return {"message": "Driver added to your fleet", "driver_id": str(match.id), "full_name": match.full_name}


@router.post("/drivers/invite")
async def vendor_invite_driver(
    payload: DriverInviteRequest,
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    """Vendor creates a pending driver account; the driver activates via invite link."""
    from app.models.user import User, UserProfile, UserRole
    from app.core.security import create_access_token
    from app.config import settings as app_settings

    vendor_id = UUID(current_vendor["id"])
    email = (payload.email or "").strip().lower()
    if email:
        exists = await db.execute(select(User).where(User.email == email))
        if exists.scalar_one_or_none():
            raise HTTPException(http_status.HTTP_409_CONFLICT, detail="A user with that email already exists.")
    phone_exists = await db.execute(select(User).where(User.phone_number == payload.phone))
    if phone_exists.scalar_one_or_none():
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail="A user with that phone number already exists.")

    parts = payload.full_name.strip().split()
    first = parts[0] if parts else "Driver"
    last = " ".join(parts[1:]) or "Driver"
    if not email:
        email = "driver-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10)) + "@invite.burncost.test"

    user = User(
        email=email,
        phone_number=payload.phone,
        password_hash=None,
        role=UserRole("driver"),
        status="pending",
    )
    db.add(user)
    await db.flush()

    db.add(UserProfile(
        user_id=user.id,
        first_name=first,
        last_name=last,
        business_name=None,
    ))
    driver = DriverProfile(
        user_id=user.id,
        vendor_id=vendor_id,
        source="vendor",
        status="pending",
        full_name=payload.full_name.strip(),
        phone=payload.phone,
        availability=False,
    )
    db.add(driver)
    await db.flush()

    token = create_access_token(
        data={"sub": str(user.id), "type": "driver_invite", "phone": _norm_phone(payload.phone)},
        expires_delta=timedelta(days=7),
    )
    front = app_settings.FRONTEND_URL
    activation_link = f"{front}/driver/activate?token={token}"

    _notify(db, UUID(current_vendor["user_id"]), "Driver invite created",
            f"Send this activation link to {payload.full_name.strip()}: {activation_link}")
    await db.commit()
    return {
        "message": "Driver account created. They must activate via the invite link.",
        "driver_id": str(driver.id),
        "full_name": payload.full_name.strip(),
        "activation_link": activation_link,
    }


@router.post("/jobs", status_code=http_status.HTTP_201_CREATED)
async def vendor_create_dispatch(
    payload: DispatchCreate,
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    vendor_id = UUID(current_vendor["id"])
    order = await _load_order(db, payload.order_id)

    if not await _vendor_owns_order(db, order, vendor_id):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="This order is not from your catalog.")

    # No duplicate active dispatch for the same (order, vendor).
    dup = await db.execute(
        select(DeliveryJob).where(
            DeliveryJob.order_id == order.id,
            DeliveryJob.vendor_id == vendor_id,
            DeliveryJob.status.in_(["available", "accepted", "picked_up", "in_transit"]),
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail="An active dispatch already exists for this order.")

    vendor_row = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = vendor_row.scalar_one_or_none()

    # Pickup = vendor's base; dropoff = buyer shipping address.
    pickup_city = vendor.city if vendor else ""
    pickup_state = vendor.state if vendor else ""
    pickup_address = (vendor.business_address or "") if vendor else ""

    sa = order.shipping_address
    dropoff_city = sa.city if sa else ""
    dropoff_state = sa.state if sa else ""
    dropoff_address = sa.address_line1 if sa else ""
    dropoff_lat = Decimal(str(sa.latitude)) if sa and sa.latitude is not None else None
    dropoff_lng = Decimal(str(sa.longitude)) if sa and sa.longitude is not None else None

    job = DeliveryJob(
        job_number=_job_number(),
        order_id=order.id,
        vendor_id=vendor_id,
        assignment_type=payload.assignment_type,
        status="available",
        pickup_city=pickup_city,
        pickup_state=pickup_state,
        pickup_address=pickup_address,
        pickup_lat=Decimal(str(payload.pickup_lat)) if payload.pickup_lat is not None else None,
        pickup_lng=Decimal(str(payload.pickup_lng)) if payload.pickup_lng is not None else None,
        dropoff_city=dropoff_city,
        dropoff_state=dropoff_state,
        dropoff_address=dropoff_address,
        dropoff_lat=dropoff_lat,
        dropoff_lng=dropoff_lng,
        delivery_fee=Decimal(str(order.shipping_fee or 0)),
    )

    assigned_driver = None
    if payload.assignment_type == "direct":
        if not payload.driver_profile_id:
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="driver_profile_id is required for direct dispatch")
        driver_row = await db.execute(
            select(DriverProfile).where(
                DriverProfile.id == payload.driver_profile_id,
                DriverProfile.status == "active",
            )
        )
        assigned_driver = driver_row.scalar_one_or_none()
        if not assigned_driver:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Driver not found or not active")
        # DIRECT assignment auto-accepts: no driver opt-in needed.
        job.status = "accepted"
        job.accepted_at = datetime.utcnow()
        job.driver_profile_id = assigned_driver.id
        job.driver_name = assigned_driver.full_name or assigned_driver.phone or ""
        job.driver_phone = assigned_driver.phone or ""
        await _set_order_driver(order, assigned_driver)

    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Notifications.
    if assigned_driver:
        _notify(db, assigned_driver.user_id,
                "New delivery assignment",
                f"You have been assigned delivery {job.job_number} for order {order.order_number}. Auto-accepted.")
    await db.commit()

    return _job_dict(job, order_number=order.order_number)


@router.get("/jobs/mine")
async def vendor_list_dispatches(
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    vendor_id = UUID(current_vendor["id"])
    result = await db.execute(
        select(DeliveryJob)
        .options(selectinload(DeliveryJob.order))
        .where(DeliveryJob.vendor_id == vendor_id)
        .order_by(DeliveryJob.created_at.desc())
    )
    jobs = result.scalars().all()
    return [_job_dict(j, order_number=(j.order.order_number if j.order else None)) for j in jobs]


@router.post("/jobs/{job_id}/cancel")
async def vendor_cancel_dispatch(
    job_id: UUID,
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    vendor_id = UUID(current_vendor["id"])
    result = await db.execute(
        select(DeliveryJob).options(selectinload(DeliveryJob.order)).where(
            DeliveryJob.id == job_id, DeliveryJob.vendor_id == vendor_id
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Dispatch not found")
    if job.status in _TERMINAL:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Dispatch already finished")
    if job.status not in ("available", "accepted"):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Dispatch already in progress; cannot cancel")

    job.status = "cancelled"
    job.cancelled_at = datetime.utcnow()
    if job.driver_profile_id:
        drow = await db.execute(select(DriverProfile).where(DriverProfile.id == job.driver_profile_id))
        drv = drow.scalar_one_or_none()
        if drv:
            _notify(db, drv.user_id, "Delivery cancelled",
                    f"Delivery {job.job_number} for order {job.order.order_number if job.order else ''} was cancelled.")
    await db.commit()
    return {"message": "Dispatch cancelled", "job_id": str(job.id)}


# ═══════════════════════════ DRIVER ENDPOINTS ══════════════════════════

@router.get("/offers")
async def driver_list_offers(
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Open delivery offers near the driver's default location (online only)."""
    if not profile.availability:
        return []
    if profile.status != "active":
        return []

    query = (
        select(DeliveryJob)
        .options(selectinload(DeliveryJob.order))
        .where(
            DeliveryJob.status == "available",
            DeliveryJob.assignment_type == "offer",
        )
    )
    if profile.default_state:
        query = query.where(DeliveryJob.pickup_state == profile.default_state)
    if profile.default_city:
        query = query.where(DeliveryJob.pickup_city == profile.default_city)

    result = await db.execute(query.order_by(DeliveryJob.created_at.asc()))
    jobs = result.scalars().all()
    return [_job_dict(j, order_number=(j.order.order_number if j.order else None)) for j in jobs]


@router.post("/offers/{job_id}/accept")
async def driver_accept_offer(
    job_id: UUID,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    if not profile.availability:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="You are offline. Turn on availability to accept deliveries.")
    if profile.status != "active":
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Driver account is not active.")

    # Drivers may hold only one active job at a time.
    active = await db.execute(
        select(DeliveryJob.id).where(
            DeliveryJob.driver_profile_id == profile.id,
            DeliveryJob.status.in_(_ACTIVE_JOB_STATUSES),
        )
    )
    if active.first():
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail="You already have an active delivery. Finish it first.")

    # Atomic claim: only the first driver to hit this UPDATE wins.
    claimed = await db.execute(
        update(DeliveryJob)
        .where(
            DeliveryJob.id == job_id,
            DeliveryJob.status == "available",
            DeliveryJob.assignment_type == "offer",
        )
        .values(
            status="accepted",
            driver_profile_id=profile.id,
            driver_name=profile.full_name or profile.phone or "",
            driver_phone=profile.phone or "",
            accepted_at=datetime.utcnow(),
        )
    )
    if claimed.rowcount != 1:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail="This delivery was already taken or is no longer available.")

    result = await db.execute(
        select(DeliveryJob).options(selectinload(DeliveryJob.order)).where(DeliveryJob.id == job_id)
    )
    job = result.scalar_one()
    order = job.order
    await _set_order_driver(order, profile)
    await db.commit()

    vrow = await db.execute(select(Vendor).where(Vendor.id == job.vendor_id))
    vendor = vrow.scalar_one_or_none()
    if vendor:
        _notify(db, vendor.user_id, "Delivery accepted",
                f"Driver {profile.full_name} accepted delivery {job.job_number} for order {order.order_number}.")
    await db.commit()

    return _job_dict(job, order_number=(order.order_number if order else None))


@router.get("/jobs/mine")
async def driver_list_my_jobs(
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeliveryJob)
        .options(selectinload(DeliveryJob.order))
        .where(DeliveryJob.driver_profile_id == profile.id)
        .order_by(DeliveryJob.created_at.desc())
    )
    jobs = result.scalars().all()
    return [_job_dict(j, order_number=(j.order.order_number if j.order else None)) for j in jobs]


async def _driver_transition(db: AsyncSession, profile: DriverProfile, job_id: UUID, target: str):
    result = await db.execute(
        select(DeliveryJob).options(selectinload(DeliveryJob.order)).where(
            DeliveryJob.id == job_id, DeliveryJob.driver_profile_id == profile.id
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Job not found or not assigned to you")
    if job.status in _TERMINAL:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Job already finished")

    if target == "picked_up":
        if job.status not in ("accepted", "picked_up"):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Job must be accepted before pickup")
        job.status = "picked_up"
        job.picked_up_at = datetime.utcnow()
        _advance_order_status(job.order, "shipped")
    elif target == "delivered":
        if job.status not in ("accepted", "picked_up", "in_transit"):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Job must be in progress to deliver")
        job.status = "delivered"
        job.delivered_at = datetime.utcnow()
        _advance_order_status(job.order, "delivered")
    else:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Unknown action")

    # Credit earnings for an independent, marketplace (non-fleet) driver on delivery.
    if target == "delivered":
        await _credit_driver_earnings(db, job)

    await db.commit()
    await db.refresh(job)

    order_number = job.order.order_number if job.order else ""
    if job.order and job.order.user:
        _notify(db, job.order.user_id,
                "Delivery update",
                f"Delivery {job.job_number} for order {order_number} is now {job.status.replace('_', ' ')}.")
    await db.commit()

    return {"message": f"Job marked {job.status}", "job_id": str(job.id)}


@router.post("/jobs/{job_id}/picked_up")
async def driver_mark_picked_up(
    job_id: UUID,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    return await _driver_transition(db, profile, job_id, "picked_up")


@router.post("/jobs/{job_id}/delivered")
async def driver_mark_delivered(
    job_id: UUID,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    return await _driver_transition(db, profile, job_id, "delivered")


# ── Proof of delivery (Phase 1A) ─────────────────────────────────────

def _proof_dict(p: Optional[DeliveryProof]) -> Optional[dict]:
    if not p:
        return None
    return {
        "id": str(p.id),
        "delivery_job_id": str(p.delivery_job_id),
        "pod_type": p.pod_type,
        "file_url": p.file_url,
        "note": p.note,
        "captured_lat": float(p.captured_lat) if p.captured_lat is not None else None,
        "captured_lng": float(p.captured_lng) if p.captured_lng is not None else None,
        "submitted_by": p.submitted_by,
        "captured_at": p.captured_at,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.post("/jobs/{job_id}/pod")
async def driver_submit_proof(
    job_id: UUID,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
    pod_type: str = Form("photo"),
    note: Optional[str] = Form(None),
    lat: Optional[float] = Form(None),
    lng: Optional[float] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    """Driver captures proof of delivery for an assigned, in-progress job.

    Accepts a multipart upload: an optional image file (photo / signature scan)
    plus optional textual note and geotag. At most one proof row per job: a
    re-capture updates the existing row instead of creating duplicates.
    """
    if profile.status != "active":
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Driver account is not active.")
    if pod_type not in ("photo", "signature", "note"):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="pod_type must be photo, signature or note")

    result = await db.execute(
        select(DeliveryJob).options(selectinload(DeliveryJob.order)).where(
            DeliveryJob.id == job_id, DeliveryJob.driver_profile_id == profile.id
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Job not found or not assigned to you")
    if job.status in _TERMINAL:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Delivery already finished; proof cannot be changed")

    file_url = None
    if file is not None and file.filename:
        content = await file.read()
        if not content:
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
        if len(content) > 8 * 1024 * 1024:
            raise HTTPException(http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large (max 8MB)")
        ext = (file.filename.rsplit(".", 1)[1] if "." in file.filename else "jpg")
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        file_url = await StorageService().upload_file(
            file=content,
            folder="delivery-proofs",
            filename=f"{job.job_number}-{suffix}.{ext[:10]}",
        )

    if pod_type == "note" and not (note or "").strip() and not file_url:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Provide a note or file for note-type proof")
    if pod_type in ("photo", "signature") and not file_url:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=f"An image is required for {pod_type} proof")

    now = datetime.utcnow()
    existing = (
        await db.execute(select(DeliveryProof).where(DeliveryProof.delivery_job_id == job_id))
    ).scalar_one_or_none()
    if existing:
        existing.pod_type = pod_type
        if file_url:
            existing.file_url = file_url
        if note is not None:
            existing.note = note
        if lat is not None:
            existing.captured_lat = Decimal(str(lat))
        if lng is not None:
            existing.captured_lng = Decimal(str(lng))
        existing.submitted_by = "driver"
        existing.captured_at = now
    else:
        existing = DeliveryProof(
            delivery_job_id=job_id,
            pod_type=pod_type,
            file_url=file_url,
            note=note,
            captured_lat=Decimal(str(lat)) if lat is not None else None,
            captured_lng=Decimal(str(lng)) if lng is not None else None,
            submitted_by="driver",
            captured_at=now,
        )
        db.add(existing)
    await db.commit()
    await db.refresh(existing)

    order_number = job.order.order_number if job.order else ""
    if job.order:
        _notify(db, job.order.user_id, "Delivery proof added",
                f"Driver {profile.full_name or profile.phone or ''} submitted proof for delivery {job.job_number} (order {order_number}).")
    await db.commit()

    return {"message": "Proof of delivery saved", "proof": _proof_dict(existing)}


@router.get("/jobs/{job_id}/pod")
async def get_delivery_proof(
    job_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the stored proof of delivery for a job.

    Accessible to the delivery driver, the owning vendor, the buyer on the order,
    or admin — mirroring the role guards already used by the job tracking endpoint.
    """
    row = await db.execute(
        select(DeliveryProof, DeliveryJob, Order)
        .join(DeliveryJob, DeliveryJob.id == DeliveryProof.delivery_job_id)
        .join(Order, Order.id == DeliveryJob.order_id)
        .where(DeliveryProof.delivery_job_id == job_id)
    )
    first = row.first()
    if not first:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Proof of delivery not found for this job")
    proof, job, order = first

    role = current_user.role.value if current_user.role else ""
    if role not in ("admin", "super_admin"):
        if role == "vendor":
            vrow = await db.execute(select(Vendor).where(Vendor.user_id == current_user.id))
            vendor = vrow.scalar_one_or_none()
            if not vendor or str(vendor.id) != str(job.vendor_id):
                raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Not your delivery")
        elif role == "driver":
            drow = await db.execute(select(DriverProfile).where(DriverProfile.user_id == current_user.id))
            dprofile = drow.scalar_one_or_none()
            if not dprofile or not job.driver_profile_id or str(dprofile.id) != str(job.driver_profile_id):
                raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Not your delivery")
        else:
            if order.user_id != current_user.id:
                raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Not your order")

    return _proof_dict(proof)


# ── Driver ratings (Phase 1B) ───────────────────────────────────────

@router.post("/jobs/{job_id}/rating")
async def buyer_rate_delivery(
    job_id: UUID,
    payload: RatingCreate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A buyer rates the delivery driver after their order has been delivered.

    Only the buyer who owns the order on this delivery job may rate, and only
    once the job is delivered (verified purchase). One rating per job; re-rating
    updates the existing row.
    """
    result = await db.execute(
        select(DeliveryJob).options(selectinload(DeliveryJob.order)).where(DeliveryJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Delivery job not found")
    if job.status != "delivered":
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Only delivered deliveries can be rated")
    if job.order is None or job.order.user_id != current_user.id:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Only the buyer on this order can rate this delivery")
    if not job.driver_profile_id:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="No driver was assigned to this delivery")

    now = datetime.utcnow()
    existing = (
        await db.execute(select(DeliveryRating).where(DeliveryRating.delivery_job_id == job_id))
    ).scalar_one_or_none()
    if existing:
        existing.rating = payload.rating
        if payload.comment is not None:
            existing.comment = payload.comment
        existing.updated_at = now
    else:
        existing = DeliveryRating(
            delivery_job_id=job_id,
            driver_profile_id=job.driver_profile_id,
            user_id=current_user.id,
            reviewer_name=getattr(current_user, "email", None) or "Builder",
            rating=payload.rating,
            comment=payload.comment,
            is_verified_purchase=True,
            created_at=now,
        )
        db.add(existing)
    await db.commit()

    # Recompute the driver's aggregate rating/count.
    avg = (await db.execute(
        select(func.avg(DeliveryRating.rating)).where(DeliveryRating.driver_profile_id == job.driver_profile_id)
    )).scalar()
    count = (await db.execute(
        select(func.count(DeliveryRating.id)).where(DeliveryRating.driver_profile_id == job.driver_profile_id)
    )).scalar()

    drow = await db.execute(select(DriverProfile).where(DriverProfile.id == job.driver_profile_id))
    driver = drow.scalar_one_or_none()
    if driver:
        driver.rating = Decimal(str(round(float(avg or 0), 2)))
        driver.rating_count = int(count or 0)
        driver.updated_at = now
        await db.commit()

    return {"message": "Driver rating saved", "driver_profile_id": str(job.driver_profile_id), "rating": payload.rating}


@router.get("/drivers/{driver_profile_id}/rating")
async def get_driver_rating(
    driver_profile_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read a driver's aggregate rating and recent reviews (any authenticated user)."""
    drow = await db.execute(select(DriverProfile).where(DriverProfile.id == driver_profile_id))
    driver = drow.scalar_one_or_none()
    if not driver:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Driver not found")

    reviews = (await db.execute(
        select(DeliveryRating)
        .where(DeliveryRating.driver_profile_id == driver_profile_id)
        .order_by(DeliveryRating.created_at.desc())
        .limit(20)
    )).scalars().all()

    return {
        "driver_id": str(driver.id),
        "full_name": driver.full_name or driver.phone or "",
        "rating": float(driver.rating) if driver.rating is not None else None,
        "rating_count": driver.rating_count or 0,
        "reviews": [
            {
                "id": str(r.id),
                "rating": r.rating,
                "comment": r.comment,
                "reviewer_name": r.reviewer_name,
                "verified": bool(r.is_verified_purchase),
                "created_at": r.created_at,
            }
            for r in reviews
        ],
    }


@router.get("/my/ratable-deliveries")
async def buyer_ratable_deliveries(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delivered deliveries for the current buyer that have not been rated yet.

    Powers the post-delivery "rate your driver" prompt. A delivery is ratable
    when its job is delivered, a driver was assigned, and no DeliveryRating row
    exists for that job yet.
    """
    rows = (await db.execute(
        select(DeliveryJob, Order)
        .join(Order, Order.id == DeliveryJob.order_id)
        .outerjoin(DeliveryRating, DeliveryRating.delivery_job_id == DeliveryJob.id)
        .where(
            DeliveryJob.status == "delivered",
            DeliveryJob.driver_profile_id.isnot(None),
            Order.user_id == current_user.id,
            DeliveryRating.id.is_(None),
        )
        .order_by(DeliveryJob.delivered_at.desc())
    )).all()

    return [
        {
            "job_id": str(job.id),
            "job_number": job.job_number,
            "order_number": order.order_number,
            "driver_profile_id": str(job.driver_profile_id),
            "driver_name": job.driver_name,
            "driver_phone": job.driver_phone,
            "delivered_at": job.delivered_at,
        }
        for job, order in rows
    ]


@router.get("/me/earnings")
async def driver_earnings_summary(
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Earnings summary + ledger history for the authenticated independent driver.

    Shows the pending balance owed and the next (first-of-month) payout date.
    Payout disbursement itself runs in the Settlement & Disbursement phase.
    """
    pending_total = (await db.execute(
        select(func.coalesce(func.sum(DriverEarnings.net_amount), 0))
        .where(DriverEarnings.driver_profile_id == profile.id, DriverEarnings.status == "pending")
    )).scalar()

    rows = (await db.execute(
        select(DriverEarnings, DeliveryJob)
        .join(DeliveryJob, DeliveryJob.id == DriverEarnings.delivery_job_id)
        .where(DriverEarnings.driver_profile_id == profile.id)
        .order_by(DriverEarnings.created_at.desc())
    )).all()

    paid_total = round(sum(float(e.net_amount) for e, _ in rows if e.status == "paid"), 2)

    today = datetime.utcnow()
    next_payout = (today.replace(day=1) + timedelta(days=32)).replace(day=1)

    return {
        "driver_profile_id": str(profile.id),
        "full_name": profile.full_name,
        "pending_balance": float(pending_total or 0),
        "paid_total": paid_total,
        "next_payout_date": next_payout.date().isoformat(),
        "transactions": [
            {
                "id": str(e.id),
                "delivery_job_id": str(e.delivery_job_id),
                "job_number": job.job_number if job else None,
                "gross_amount": float(e.gross_amount),
                "commission_amount": float(e.commission_amount),
                "net_amount": float(e.net_amount),
                "status": e.status,
                "paid_at": e.paid_at,
                "created_at": e.created_at,
            }
            for e, job in rows
        ],
    }


# ── Live location (Phase 7) ─────────────────────────────────────────
@router.post("/driver/location")
async def driver_report_location(
    payload: DriverLiveLocation,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    if profile.status != "active":
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Driver account is not active.")

    # Resolve the driver current active job (explicit or the latest one).
    job = None
    if payload.job_id:
        jrow = await db.execute(
            select(DeliveryJob).where(
                DeliveryJob.id == payload.job_id,
                DeliveryJob.driver_profile_id == profile.id,
                DeliveryJob.status.in_(_ACTIVE_JOB_STATUSES),
            )
        )
        job = jrow.scalar_one_or_none()
        if not job:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Job not found or not assigned to you.")
    if job is None:
        jrow = await db.execute(
            select(DeliveryJob)
            .where(
                DeliveryJob.driver_profile_id == profile.id,
                DeliveryJob.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .order_by(DeliveryJob.created_at.desc())
        )
        job = jrow.scalars().first()

    now = datetime.utcnow()
    profile.current_lat = Decimal(str(payload.lat))
    profile.current_lng = Decimal(str(payload.lng))
    profile.current_location_updated_at = now
    db.add(DriverLocationUpdate(
        driver_profile_id=profile.id,
        delivery_job_id=job.id if job else None,
        lat=Decimal(str(payload.lat)),
        lng=Decimal(str(payload.lng)),
        accuracy=Decimal(str(payload.accuracy)) if payload.accuracy is not None else None,
        recorded_at=now,
    ))

    resp: dict = {"driver_id": str(profile.id), "recorded_at": now}
    if job:
        dist = _haversine_km(payload.lat, payload.lng, job.dropoff_lat, job.dropoff_lng)
        resp["job_id"] = str(job.id)
        resp["status"] = job.status
        resp["dropoff_lat"] = float(job.dropoff_lat) if job.dropoff_lat is not None else None
        resp["dropoff_lng"] = float(job.dropoff_lng) if job.dropoff_lng is not None else None
        resp["distance_to_dropoff_km"] = dist
        resp["near_dropoff"] = bool(dist is not None and dist <= 0.5)
    await db.commit()
    return resp


@router.get("/jobs/{job_id}/track")
async def track_job(
    job_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        select(DeliveryJob, Order, DriverProfile)
        .join(Order, Order.id == DeliveryJob.order_id)
        .outerjoin(DriverProfile, DriverProfile.id == DeliveryJob.driver_profile_id)
        .where(DeliveryJob.id == job_id)
    )
    first = row.first()
    if not first:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Job not found")
    job, order, driver = first
    role = current_user.role.value if current_user.role else ""
    if role not in ("admin", "super_admin"):
        if role == "vendor":
            vrow = await db.execute(select(Vendor).where(Vendor.user_id == current_user.id))
            vendor = vrow.scalar_one_or_none()
            if not vendor or str(vendor.id) != str(job.vendor_id):
                raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Not your dispatch.")
        elif role == "driver":
            drow = await db.execute(select(DriverProfile).where(DriverProfile.user_id == current_user.id))
            dprofile = drow.scalar_one_or_none()
            if not dprofile or not job.driver_profile_id or str(dprofile.id) != str(job.driver_profile_id):
                raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Not your delivery.")
        else:
            if order.user_id != current_user.id:
                raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Not your order.")

    return {
        "job_id": str(job.id),
        "job_number": job.job_number,
        "status": job.status,
        "order_number": order.order_number,
        "driver": {
            "id": str(driver.id) if driver else None,
            "name": driver.full_name if driver else None,
            "phone": driver.phone if driver else None,
        },
        "latest_location": {
            "lat": float(driver.current_lat) if driver and driver.current_lat is not None else None,
            "lng": float(driver.current_lng) if driver and driver.current_lng is not None else None,
            "recorded_at": driver.current_location_updated_at if driver else None,
        } if driver else None,
        "pickup": {
            "lat": float(job.pickup_lat) if job.pickup_lat is not None else None,
            "lng": float(job.pickup_lng) if job.pickup_lng is not None else None,
            "label": ", ".join([x for x in [job.pickup_address, job.pickup_city, job.pickup_state] if x]),
        },
        "dropoff": {
            "lat": float(job.dropoff_lat) if job.dropoff_lat is not None else None,
            "lng": float(job.dropoff_lng) if job.dropoff_lng is not None else None,
            "label": ", ".join([x for x in [job.dropoff_address, job.dropoff_city, job.dropoff_state] if x]),
        },
    }


@router.get("/jobs/by-order/{order_ref}")
async def list_jobs_by_order(
    order_ref: str,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await _load_order(db, order_ref)
    role = current_user.role.value if current_user.role else ""
    if role not in ("admin", "super_admin") and order.user_id != current_user.id:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Not your order.")
    result = await db.execute(
        select(DeliveryJob)
        .where(DeliveryJob.order_id == order.id)
        .order_by(DeliveryJob.created_at.desc())
    )
    jobs = result.scalars().all()
    return {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "jobs": [_job_dict(j, order_number=order.order_number) for j in jobs],
    }