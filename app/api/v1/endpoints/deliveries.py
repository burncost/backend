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
import json
import math
import random
import string
import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile, status as http_status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_driver_profile, get_current_verified_vendor, get_current_user
from app.config import settings
from app.core.database import get_db
from app.core.live_events import iter_job_locations, publish_job_location
from app.core.ratelimit import rate_limit
from app.models.driver import DriverProfile, DriverLocationUpdate
from app.models.delivery import DeliveryJob
from app.models.delivery_event import DeliveryJobEvent
from app.models.delivery_proof import DeliveryProof
from app.models.delivery_rating import DeliveryRating
from app.models.driver_earnings import DriverEarnings
from app.models.notification import Notification
from app.models.order import Order, OrderItem
from app.models.vendor import Vendor
from app.models.vendor_address import VendorAddress
from app.services.geo_providers import (
    GeoUnavailable,
    reverse_place,
    search_places,
    snap_to_road,
)
from app.utils.storage import StorageService
from app.utils.geo_exif import extract_photo_metadata

router = APIRouter()

_ACTIVE_JOB_STATUSES = ("accepted", "picked_up", "in_transit")
_TERMINAL = ("delivered", "cancelled", "expired")

# Burncost commission taken on each independent-driver delivery payout (net of
# the fee before it reaches the driver). Kept at 0 by default so no driver is
# charged until this rate is deliberately configured; payout runs land in the
# Settlement & Disbursement phase.
_DRIVER_COMMISSION_RATE = Decimal("0.00")

# Money is stored to the cent; round the same way the rest of the money stack
# does (pricing_service / settlement_service) rather than Decimal's default
# ROUND_HALF_EVEN, which can shave a cent off a half-cent commission.
_CENT = Decimal("0.01")

# Newest-N ledger rows returned to the driver portal. Without a cap a driver with
# a long history would pull their entire ledger on every 5s poll; the aggregate
# totals below are always over the *full* history regardless of this window.
_EARNINGS_TXN_LIMIT = 100


# ── Request/response shapes ────────────────────────────────────────────
class DriverLiveLocation(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    accuracy: Optional[float] = Field(None, ge=0)
    job_id: Optional[UUID] = None
    # Optional manual status transition piggy-backed on a location beat.
    status: Optional[str] = Field(None, pattern="^(picked_up|in_transit|delivered)$")
    # Optional free-text update for the buyer-facing feed.
    note: Optional[str] = Field(None, max_length=500)


class DriverJobUpdate(BaseModel):
    """Driver-authored manual update: any combination of status / note / location."""
    status: Optional[str] = Field(None, pattern="^(picked_up|in_transit|delivered)$")
    note: Optional[str] = Field(None, max_length=500)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)


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


class PodReviewRequest(BaseModel):
    """Vendor/admin outcome for a flagged proof of delivery."""
    action: str = Field(..., pattern="^(clear|reject)$")
    note: Optional[str] = Field(None, max_length=500)


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


def _pod_photo_age_seconds(taken_at, captured_at) -> Optional[int]:
    """Age of a proof photo in seconds; None when there is no usable timestamp.

    A small negative value (photo stamped slightly in the future due to clock
    skew) is returned as-is — callers treat a non-positive age as "fresh".
    """
    ts = taken_at or captured_at
    if ts is None:
        return None
    try:
        return int((datetime.utcnow() - ts).total_seconds())
    except Exception:
        return None


class DriverInviteRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., min_length=5, max_length=20)
    email: Optional[str] = None

def _job_number() -> str:
    rnd = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"DLV-{datetime.utcnow().strftime('%Y%m%d')}-{rnd}"


def _job_dict(job: DeliveryJob, order_number: Optional[str] = None, proof: Optional[DeliveryProof] = None) -> dict:
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
        # Compact POD verdict (None when no proof captured yet) so the driver UI
        # can gate "mark delivered" from server truth across reloads.
        "pod": _pod_summary(proof),
    }


def _notify_buyer_of_note(
    db: AsyncSession,
    *,
    buyer_id,
    order_number: Optional[str],
    note: str,
    actor_name: Optional[str] = None,
) -> None:
    """Mirror an authored delivery note into the buyer's notification bell.

    `DeliveryJobEvent` rows only surface once the buyer opens the order page, so
    the driver's "Send buyer an update" would otherwise go unseen. A bare GPS pin
    stays feed-only (too noisy to notify on) - callers only pass a note.
    """
    text = (note or "").strip()
    if buyer_id is None or not text:
        return
    sender = (actor_name or "Driver").strip() or "Driver"
    suffix = f" (order {order_number})" if order_number else ""
    _notify(db, buyer_id, "Delivery update", f"{sender}: {text}{suffix}", "/dashboard/orders")


def _notify(db: AsyncSession, user_id, title: str, message: str, link: str | None = None) -> None:
    db.add(Notification(user_id=user_id, type="delivery", title=title, message=message, read=False, link=link))


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
    if target == "picked_up":
        # Canonical pickup step. `shipped` is accepted as a starting point only
        # for legacy rows written before this status existed.
        if order.status.value.lower() in ("confirmed", "processing", "ready_for_pickup", "pending", "pending_payment", "shipped"):
            order.status = "picked_up"
    elif target == "shipped":
        if order.status.value.lower() in ("confirmed", "processing", "ready_for_pickup", "pending", "pending_payment"):
            order.status = "shipped"
    elif target == "in_transit":
        if order.status.value.lower() in ("picked_up", "shipped", "confirmed", "processing", "ready_for_pickup"):
            order.status = "in_transit"
    elif target == "delivered":
        order.status = "delivered"
        order.delivered_at = datetime.utcnow()
        order.payment_status = "completed"


def _write_job_event(
    db: AsyncSession,
    *,
    job: Optional[DeliveryJob],
    order_id,
    actor_type: str,
    actor_name: Optional[str],
    event_type: str,
    status: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    note: Optional[str] = None,
) -> None:
    """Append one manual update to the buyer-facing delivery feed.

    Safe no-op when there is no job/order to attach the event to, so callers
    (including the vendor order-status path) never break on missing context.
    """
    resolved_order_id = order_id if order_id is not None else (job.order_id if job else None)
    if resolved_order_id is None and job is None:
        return
    db.add(DeliveryJobEvent(
        delivery_job_id=job.id if job else None,
        order_id=resolved_order_id,
        actor_type=actor_type,
        actor_name=(actor_name or "").strip() or None,
        event_type=event_type,
        status=status,
        lat=Decimal(str(lat)) if lat is not None else None,
        lng=Decimal(str(lng)) if lng is not None else None,
        note=note,
    ))


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
    gross = Decimal(str(job.delivery_fee or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)
    commission = (gross * _DRIVER_COMMISSION_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
    db.add(DriverEarnings(
        driver_profile_id=job.driver_profile_id,
        delivery_job_id=job.id,
        gross_amount=gross,
        commission_amount=commission,
        # Quantize the net too: gross - commission must never carry sub-cent
        # precision into the ledger the payout runner reads.
        net_amount=(gross - commission).quantize(_CENT, rounding=ROUND_HALF_UP),
        status="pending",
    ))


# ═══════════════════════ ADDRESS SEARCH (GEOCODING PROXY) ═══════════════
# The checkout / dispatch map pickers need to turn a typed address into a pin.
# Browsers cannot call Nominatim directly (it requires a descriptive
# User-Agent, forbids key-less high-volume use and would expose the user's IP),
# so we proxy it server-side behind auth + a rate limit and cache repeats so a
# user typing an address cannot fan out into dozens of upstream calls.
# The providers themselves live in `app/services/geo_providers.py`.
_GEO_CACHE_TTL = 600.0  # seconds a resolved query stays warm
_GEO_CACHE_MAX = 500    # hard cap so a cache cannot grow unbounded
_geo_cache: Dict[str, Tuple[float, List[dict]]] = {}
_geo_rev_cache: Dict[str, Tuple[float, Any]] = {}


def _cache_get(store: Dict[str, Any], key: str):
    hit = store.get(key)
    if not hit:
        return None
    stored_at, value = hit
    if time.time() - stored_at > _GEO_CACHE_TTL:
        store.pop(key, None)
        return None
    return value


def _cache_put(store: Dict[str, Any], key: str, value) -> None:
    if len(store) >= _GEO_CACHE_MAX:
        # Drop the oldest entry (dicts preserve insertion order).
        store.pop(next(iter(store)), None)
    store[key] = (time.time(), value)


@router.get("/geo/search")
async def geo_search(
    q: str = "",
    limit: int = 5,
    current_user = Depends(get_current_user),
):
    """Free-text address / landmark lookup for the delivery map pickers.

    Returns `{"results": [{lat, lng, label, city, state, country}]}`. The
    provider chain (Nominatim NG-biased -> Nominatim unfiltered -> Photon, or
    Google when a key is configured) is what lets landmark queries such as
    "ecwa seminary church jos" resolve instead of coming back empty. Rate
    limited per user and cached briefly so the map picker can debounce freely.
    """
    query = (q or "").strip()
    if len(query) < 3:
        return {"results": []}
    try:
        limit_n = max(1, min(int(limit or 5), 8))
    except (TypeError, ValueError):
        limit_n = 5

    allowed = await rate_limit(30, 60, "geo", getattr(current_user, "id", "anon"))
    if not allowed:
        raise HTTPException(
            http_status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many address searches — please wait a moment.",
        )

    cache_key = f"{query.lower()}|{limit_n}"
    cached = _cache_get(_geo_cache, cache_key)
    if cached is not None:
        return {"results": cached}

    try:
        results = await search_places(query, limit_n)
    except GeoUnavailable as e:
        # Degrade loudly but safely: the picker keeps working by letting the
        # user drop a pin or use their current location.
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Address search is temporarily unavailable — drop a pin on the map instead.",
        ) from e

    _cache_put(_geo_cache, cache_key, results)
    return {"results": results}


@router.get("/geo/reverse")
async def geo_reverse(
    lat: float,
    lng: float,
    current_user = Depends(get_current_user),
):
    """Describe a pin: its area name, and how far it sits from the nearest road.

    Powers the "Pinned: <area> (+/- 30 m)" line and the imprecise-location
    warning in the map pickers. Never raises on an upstream failure — the pin
    is still a valid pin, we simply cannot describe it, so the caller degrades
    to raw coordinates.
    """
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Coordinates out of range.")

    allowed = await rate_limit(60, 60, "geo-rev", getattr(current_user, "id", "anon"))
    if not allowed:
        raise HTTPException(
            http_status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many location lookups — please wait a moment.",
        )

    # ~1 m precision: the cache key, so a re-tapped pin reuses one upstream call.
    cache_key = f"{round(lat, 5)}|{round(lng, 5)}"
    cached = _cache_get(_geo_rev_cache, cache_key)
    if cached is not None:
        return cached

    label = city = state = None
    try:
        place = await reverse_place(lat, lng)
        label = place.get("label") or None
        city = place.get("city") or None
        state = place.get("state") or None
    except GeoUnavailable:
        pass  # undescribed, but the pin still stands

    snap = await snap_to_road(lat, lng)
    payload = {
        "label": label,
        "city": city,
        "state": state,
        "snapped_lat": snap.get("lat") if snap else None,
        "snapped_lng": snap.get("lng") if snap else None,
        "road_snap_distance_m": snap.get("distance_m") if snap else None,
    }
    _cache_put(_geo_rev_cache, cache_key, payload)
    return payload



# ═══════════════════════════ VENDOR ENDPOINTS ══════════════════════════

class VendorPickupPoint(BaseModel):
    """A vendor's pinned dispatch origin (saved to `vendor_addresses`)."""
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    label: Optional[str] = Field(None, max_length=500)


def _vendor_primary_address(payload: VendorPickupPoint, vendor: Optional[Vendor]) -> VendorAddress:
    """Build the coordinates row when a vendor has no saved address yet.

    `vendor_addresses.address_line1/city/state` are NOT NULL, so the pinned
    label (or the vendor's own registered address) fills them in.
    """
    return VendorAddress(
        vendor_id=vendor.id if vendor else None,
        address_type="warehouse",
        address_line1=(payload.label or (vendor.business_address if vendor else None)
                       or (vendor.city if vendor else None) or "Dispatch pickup point"),
        city=(vendor.city if vendor and vendor.city else "—"),
        state=(vendor.state if vendor and vendor.state else "—"),
        latitude=Decimal(str(payload.lat)),
        longitude=Decimal(str(payload.lng)),
        is_primary=True,
    )


@router.get("/pickup")
async def vendor_get_pickup_point(
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    """The vendor's saved pickup point (None until they pin one)."""
    vendor_id = UUID(current_vendor["id"])
    row = (await db.execute(
        select(VendorAddress)
        .where(VendorAddress.vendor_id == vendor_id)
        .order_by(VendorAddress.is_primary.desc(), VendorAddress.created_at.desc())
    )).scalars().first()
    if not row or row.latitude is None or row.longitude is None:
        return {"lat": None, "lng": None, "label": None}
    return {
        "lat": float(row.latitude),
        "lng": float(row.longitude),
        "label": row.address_line1,
        "address_id": str(row.id),
    }


@router.post("/pickup")
async def vendor_set_pickup_point(
    payload: VendorPickupPoint,
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    """Save/refresh the vendor's default pickup coordinates.

    Dispatches fall back to this point when the vendor does not pin one at
    creation time, so the tracking map always has an origin to draw from.
    """
    vendor_id = UUID(current_vendor["id"])
    vendor = (await db.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one_or_none()

    row = (await db.execute(
        select(VendorAddress)
        .where(VendorAddress.vendor_id == vendor_id)
        .order_by(VendorAddress.is_primary.desc(), VendorAddress.created_at.desc())
    )).scalars().first()

    if row:
        row.latitude = Decimal(str(payload.lat))
        row.longitude = Decimal(str(payload.lng))
        # Only one primary address per vendor.
        if not row.is_primary:
            others = (await db.execute(
                select(VendorAddress).where(
                    VendorAddress.vendor_id == vendor_id, VendorAddress.id != row.id
                )
            )).scalars().all()
            for o in others:
                o.is_primary = False
            row.is_primary = True
        if payload.label:
            row.address_line1 = payload.label
    else:
        db.add(_vendor_primary_address(payload, vendor))

    await db.commit()
    return {"message": "Pickup point saved", "lat": payload.lat, "lng": payload.lng}


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
    """List active drivers the vendor can direct-assign.

    Restricted to drivers the vendor may legitimately use: independent drivers
    (no fleet) plus the vendor's own fleet. Another vendor's fleet driver is
    excluded. Offline drivers stay visible (so the vendor can see who exists)
    with `availability` surfaced for the UI to label.
    """
    vendor_id = UUID(current_vendor["id"])
    result = await db.execute(
        select(DriverProfile)
        .where(
            DriverProfile.status == "active",
            or_(
                DriverProfile.vendor_id.is_(None),
                DriverProfile.vendor_id == vendor_id,
            ),
        )
        .order_by(DriverProfile.availability.desc(), DriverProfile.created_at.desc())
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
    # Don't silently poach a driver who already belongs to another vendor's fleet.
    if match.vendor_id and str(match.vendor_id) != str(vendor_id):
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            detail="This driver is already attached to another supplier's fleet. Ask that supplier to release them first.",
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
        "email": email,
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

    # Only dispatch orders that have actually been paid — dispatching an unpaid
    # order would put a driver on the road for money that never arrived.
    paid = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    if paid != "completed":
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail="This order isn't paid yet — dispatch becomes available once payment is confirmed.",
        )

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

    # Pickup coordinates: an explicit pin from the dispatch form wins; otherwise
    # fall back to the vendor's saved primary address so the tracking map always
    # has an origin marker instead of showing the driver alone.
    pickup_lat = payload.pickup_lat
    pickup_lng = payload.pickup_lng
    if pickup_lat is None or pickup_lng is None:
        vaddr = (await db.execute(
            select(VendorAddress)
            .where(VendorAddress.vendor_id == vendor_id, VendorAddress.latitude.isnot(None))
            .order_by(VendorAddress.is_primary.desc(), VendorAddress.created_at.desc())
        )).scalars().first()
        if vaddr:
            if pickup_lat is None:
                pickup_lat = float(vaddr.latitude)
            if pickup_lng is None and vaddr.longitude is not None:
                pickup_lng = float(vaddr.longitude)
            if not pickup_address:
                pickup_address = vaddr.address_line1 or ""
            if not pickup_city:
                pickup_city = vaddr.city or ""
            if not pickup_state:
                pickup_state = vaddr.state or ""

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
        pickup_lat=Decimal(str(pickup_lat)) if pickup_lat is not None else None,
        pickup_lng=Decimal(str(pickup_lng)) if pickup_lng is not None else None,
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
async def list_my_jobs(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dispatches for the caller, dispatched by role.

    Vendors see the jobs they created; drivers see the jobs assigned to them.
    This used to be two handlers on the same path, so the vendor handler shadowed
    the driver one and any driver hitting it received 403 — breaking the driver
    dashboard. Role-based dispatch here serves both clients from one route.
    """
    role = current_user.role.value if getattr(current_user, "role", None) is not None and hasattr(current_user.role, "value") else str(getattr(current_user, "role", "") or "")

    if role == "driver":
        drow = await db.execute(select(DriverProfile).where(DriverProfile.user_id == current_user.id))
        profile = drow.scalar_one_or_none()
        if not profile:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Driver profile not found")
        query = (
            select(DeliveryJob)
            .options(selectinload(DeliveryJob.order))
            .where(DeliveryJob.driver_profile_id == profile.id)
        )
    elif role in ("vendor", "admin", "super_admin"):
        vrow = await db.execute(select(Vendor).where(Vendor.user_id == current_user.id))
        vendor = vrow.scalar_one_or_none()
        if not vendor:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Vendor profile not found")
        status_value = vendor.verification_status.value if hasattr(vendor.verification_status, "value") else str(vendor.verification_status or "")
        if status_value in ("suspended", "deactivated", "rejected"):
            raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Your account is not active for selling. Please contact support.")
        query = (
            select(DeliveryJob)
            .options(selectinload(DeliveryJob.order))
            .where(DeliveryJob.vendor_id == vendor.id)
        )
    else:
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Logistics are not available for this account type.")

    result = await db.execute(query.order_by(DeliveryJob.created_at.desc()))
    jobs = result.scalars().all()
    # Batch-load proofs in one query so the driver/vendor UIs can show a POD
    # verdict without an N+1 round-trip per job.
    proofs = {}
    if jobs:
        proof_rows = await db.execute(
            select(DeliveryProof).where(DeliveryProof.delivery_job_id.in_([j.id for j in jobs]))
        )
        proofs = _load_proofs_map(proof_rows.scalars().all())
    return [
        _job_dict(j, order_number=(j.order.order_number if j.order else None), proof=proofs.get(j.id))
        for j in jobs
    ]


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

_AREA_ALIASES = {
    "fct": "fctabuja",
    "fctabuja": "fctabuja",
    "abuja": "fctabuja",
    "federalcapitalterritory": "fctabuja",
}


def _area_key(value: Optional[str]) -> str:
    """Case/punctuation-insensitive key for an area string ("FCT (Abuja)" -> "fctabuja")."""
    key = "".join(ch for ch in (value or "").lower() if ch.isalnum())
    return _AREA_ALIASES.get(key, key)


def _area_tier(job: DeliveryJob, state_key: str, city_key: str) -> int:
    """0 = driver city, 1 = driver state, 2 = outside the driver area."""
    if city_key and _area_key(job.pickup_city) == city_key:
        return 0
    if state_key and _area_key(job.pickup_state) == state_key:
        return 1
    return 2


def _offer_in_area(job: DeliveryJob, state_key: str, city_key: str) -> bool:
    """Whether an open offer sits in the driver state or city."""
    if state_key and _area_key(job.pickup_state) == state_key:
        return True
    return bool(city_key) and _area_key(job.pickup_city) == city_key


def _rank_offers(
    jobs: List[DeliveryJob], state: Optional[str], city: Optional[str]
) -> List[DeliveryJob]:
    """Order open offers for a driver: exact city, then same state, then the rest.

    The area is a ranking, not a hard filter. A vendor pickup city is one of many in
    the driver state (a driver based in "Gwarinpa" must still see a "Wuse" pickup),
    so requiring an exact city match hid every offer. And when the driver state has
    nothing open we still return the other open offers, ranked last, instead of an
    empty board with no explanation.
    """
    state_key = _area_key(state)
    city_key = _area_key(city)
    ranked = sorted(
        jobs,
        key=lambda j: (_area_tier(j, state_key, city_key), j.created_at or datetime.min),
    )
    if state_key or city_key:
        ranked = [j for j in ranked if _offer_in_area(j, state_key, city_key)] or ranked
    return ranked


@router.get("/offers")
async def driver_list_offers(
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Open delivery offers for the driver's area, best match first (online only)."""
    if not profile.availability:
        return []
    if profile.status != "active":
        return []

    result = await db.execute(
        select(DeliveryJob)
        .options(selectinload(DeliveryJob.order))
        .where(
            DeliveryJob.status == "available",
            DeliveryJob.assignment_type == "offer",
        )
        .order_by(DeliveryJob.created_at.asc())
    )
    jobs = _rank_offers(result.scalars().all(), profile.default_state, profile.default_city)
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
                f"Driver {profile.full_name} accepted delivery {job.job_number} for order {order.order_number}.",
                "/supplier-dashboard?tab=orders")
    await db.commit()

    return _job_dict(job, order_number=(order.order_number if order else None))


# NOTE: the driver-scoped GET /jobs/mine handler was removed — it duplicated the
# role-aware handler above and, being registered second, was unreachable (FastAPI
# serves the first match). Drivers are now served by GET /jobs/mine directly.


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
        _advance_order_status(job.order, "picked_up")
    elif target == "in_transit":
        if job.status not in ("accepted", "picked_up", "in_transit"):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Job must be accepted or picked up before transit")
        job.status = "in_transit"
        job.in_transit_at = datetime.utcnow()
        _advance_order_status(job.order, "in_transit")
    elif target == "delivered":
        if job.status not in ("accepted", "picked_up", "in_transit"):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Job must be in progress to deliver")
        # HARD GATE: a fresh, geotagged photo proof must exist before a job can be
        # marked delivered. Distance is advisory (recorded + flagged for review)
        # and never blocks; only a missing photo or a missing/stale capture time
        # does.
        proof = (
            await db.execute(select(DeliveryProof).where(DeliveryProof.delivery_job_id == job.id))
        ).scalar_one_or_none()
        if not proof or proof.pod_type != "photo" or not proof.file_url:
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                detail="Capture a geotagged photo of the delivery before marking it delivered.",
            )
        age = _pod_photo_age_seconds(proof.exif_taken_at, None)
        if age is None:
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                detail="The proof photo has no capture timestamp; retake it with the in-app camera.",
            )
        if age > settings.POD_MAX_PHOTO_AGE_MIN * 60:
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"The proof photo is about {max(1, age // 60)} min old; retake a fresh "
                    f"photo (max {settings.POD_MAX_PHOTO_AGE_MIN} min)."
                ),
            )
        job.status = "delivered"
        job.delivered_at = datetime.utcnow()
        _advance_order_status(job.order, "delivered")
    else:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Unknown action")

    # Record the manual status transition for the buyer-facing feed.
    _write_job_event(
        db,
        job=job,
        order_id=job.order_id,
        actor_type="driver",
        actor_name=profile.full_name or profile.phone,
        event_type="status",
        status=job.status,
    )

    # Credit earnings for an independent, marketplace (non-fleet) driver on delivery.
    if target == "delivered":
        await _credit_driver_earnings(db, job)

    # Capture everything the buyer notification needs *before* commit + refresh.
    # `db.refresh(job)` expires the eager-loaded `job.order` (and `Order.user` was
    # never eager-loaded), so dereferencing either afterwards triggers an async
    # lazy-load and raises MissingGreenlet -> a 500 on every driver transition.
    # Read the values while the relationship is still present instead.
    order_number = job.order.order_number if job.order else ""
    buyer_id = job.order.user_id if job.order else None
    job_number = job.job_number
    status_label = job.status.replace("_", " ")

    await db.commit()

    if buyer_id:
        _notify(db, buyer_id,
                "Delivery update",
                f"Delivery {job_number} for order {order_number} is now {status_label}.",
                "/dashboard/orders")
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


@router.post("/jobs/{job_id}/in_transit")
async def driver_mark_in_transit(
    job_id: UUID,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Manually advance an accepted/picked-up job to 'in transit'."""
    return await _driver_transition(db, profile, job_id, "in_transit")


@router.post("/jobs/{job_id}/update")
async def driver_submit_update(
    job_id: UUID,
    payload: DriverJobUpdate,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Driver-authored manual update: status transition, free-text note, and/or
    a manual location pin. Surfaced to the buyer alongside the live map.

    Nothing is written when the payload carries no change, so this is safe to
    call from the same UI panel that also drives the status buttons.
    """
    if payload.status is None and payload.note is None and payload.lat is None and payload.lng is None:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Provide a status, note or location")

    # A status change reuses the validated transition path (also writes its event).
    if payload.status:
        await _driver_transition(db, profile, job_id, payload.status)
        # Re-load to attach the optional note/location in a second event.
        job = (
            await db.execute(
                select(DeliveryJob)
                .options(selectinload(DeliveryJob.order))
                .where(DeliveryJob.id == job_id, DeliveryJob.driver_profile_id == profile.id)
            )
        ).scalar_one_or_none()
    else:
        job = (
            await db.execute(
                select(DeliveryJob).options(selectinload(DeliveryJob.order)).where(
                    DeliveryJob.id == job_id, DeliveryJob.driver_profile_id == profile.id
                )
            )
        ).scalar_one_or_none()
        if not job:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Job not found or not assigned to you")

    # Attach a note and/or manual location pin as its own event.
    if payload.note is not None or (payload.lat is not None and payload.lng is not None):
        event_type = "location" if (payload.lat is not None and payload.lng is not None) else "note"
        _write_job_event(
            db,
            job=job,
            order_id=job.order_id if job else None,
            actor_type="driver",
            actor_name=profile.full_name or profile.phone,
            event_type=event_type,
            lat=payload.lat,
            lng=payload.lng,
            note=payload.note,
        )
        # The feed is only visible from the order page, so the same note also
        # lands in the buyer's notification bell (a bare pin stays feed-only).
        order = job.order if job is not None else None
        _notify_buyer_of_note(
            db,
            buyer_id=order.user_id if order is not None else None,
            order_number=order.order_number if order is not None else None,
            note=payload.note or "",
            actor_name=profile.full_name or profile.phone,
        )
        # Keep the driver's live pin in sync when they manually dropped one.
        if payload.lat is not None and payload.lng is not None:
            profile.current_lat = Decimal(str(payload.lat))
            profile.current_lng = Decimal(str(payload.lng))
            profile.current_location_updated_at = datetime.utcnow()
    await db.commit()

    return {"message": "Update recorded", "job_id": str(job.id) if job else str(job_id)}


def _load_proofs_map(proofs) -> dict:
    """Index a list of DeliveryProof rows by delivery_job_id."""
    return {p.delivery_job_id: p for p in (proofs or [])}


async def _load_job_proof(db: AsyncSession, job_id) -> Optional[DeliveryProof]:
    """Fetch the single proof row (if any) for a delivery job."""
    row = await db.execute(select(DeliveryProof).where(DeliveryProof.delivery_job_id == job_id))
    return row.scalars().first()



async def _verify_pod_photo(
    db: AsyncSession,
    job: DeliveryJob,
    *,
    content: Optional[bytes],
    device_lat: Optional[float] = None,
    device_lng: Optional[float] = None,
) -> dict:
    """Score a submitted POD photo against freshness + geofence rules.

    Freshness is the hard gate (a `blocked_reason` is returned when the photo
    carries no usable capture time or is too old — the caller turns that into a
    400). Distance is advisory: it is always recorded and the row flagged for
    review, but never blocks. Never raises — a missing EXIF block degrades to
    "unverified" rather than crashing proof capture.
    """
    radius_m = settings.POD_GEOFENCE_RADIUS_M
    max_age_s = settings.POD_MAX_PHOTO_AGE_MIN * 60

    meta = extract_photo_metadata(content) if content else {}
    exif_taken_at = meta.get("exif_taken_at")
    exif_lat = meta.get("exif_lat")
    exif_lng = meta.get("exif_lng")

    # ── Freshness (hard gate) ──
    # captured_at is set to server-now at submit, so freshness must come from
    # the photo's own EXIF stamp; a photo without one cannot be trusted as live.
    age = _pod_photo_age_seconds(exif_taken_at, None)
    is_live = age is not None and -300 <= age <= max_age_s
    blocked_reason: Optional[str] = None
    if age is None:
        blocked_reason = (
            "Proof photo has no capture timestamp. Take the photo with the in-app "
            "camera so its time and location are recorded."
        )
    elif age > max_age_s:
        blocked_reason = (
            f"Proof photo is about {max(1, age // 60)} min old; take a fresh photo "
            f"(max {settings.POD_MAX_PHOTO_AGE_MIN} min)."
        )

    # ── Coordinates (advisory) ──
    if exif_lat is not None and exif_lng is not None:
        gps_source = "exif"
        coord_lat, coord_lng = exif_lat, exif_lng
    elif device_lat is not None and device_lng is not None:
        gps_source = "device"
        coord_lat, coord_lng = device_lat, device_lng
    else:
        gps_source = "none"
        coord_lat = coord_lng = None

    # Reference point: job dropoff, else the driver's last heartbeat on the job.
    ref_lat, ref_lng = job.dropoff_lat, job.dropoff_lng
    if ref_lat is None or ref_lng is None:
        row = await db.execute(
            select(DriverLocationUpdate)
            .where(DriverLocationUpdate.delivery_job_id == job.id)
            .order_by(DriverLocationUpdate.recorded_at.desc())
            .limit(1)
        )
        latest = row.scalars().first()
        if latest is not None:
            ref_lat, ref_lng = latest.lat, latest.lng

    distance_m = None
    within_geofence = None
    if coord_lat is not None and coord_lng is not None and ref_lat is not None and ref_lng is not None:
        km = _haversine_km(coord_lat, coord_lng, ref_lat, ref_lng)
        if km is not None:
            distance_m = round(km * 1000, 2)
            within_geofence = distance_m <= radius_m

    # Default: only a no-GPS photo that still passed the freshness gate needs a
    # buyer confirmation (set in the branch below).
    buyer_confirmation_required = False
    if coord_lat is None or coord_lng is None:
        if exif_taken_at is not None:
            # No GPS, but the photo still carries a usable capture time that the
            # freshness gate above already accepted. Rather than dead-ending the
            # proof in the review queue, hand the receipt to the buyer: the
            # "confirm receipt" button only activates on the photo's own capture
            # day (enforced server-side), so this is never a stalemate.
            verdict_status = "awaiting_buyer"
            verdict_note = (
                "Photo has no location data. Confirm you received this delivery "
                "to complete the order."
            )
            buyer_confirmation_required = True
        else:
            verdict_status = "unverified"
            verdict_note = "Photo has no GPS coordinates; the drop-off could not be confirmed."
    elif ref_lat is None or ref_lng is None:
        verdict_status = "unverified"
        verdict_note = "No delivery location on file to compare the photo against."
    elif within_geofence:
        verdict_status = "verified"
        verdict_note = f"Photo captured within {radius_m} m of the drop-off."
    else:
        verdict_status = "out_of_range"
        verdict_note = f"Photo captured ~{int(distance_m)} m from the drop-off (limit {radius_m} m)."

    return {
        "verification_status": verdict_status,
        # `awaiting_buyer` is resolved by the buyer, not the vendor/admin review
        # queue, so it must not be flagged for review.
        "needs_review": verdict_status not in ("verified", "awaiting_buyer"),
        "verification_note": verdict_note,
        "exif_taken_at": exif_taken_at,
        "exif_lat": exif_lat,
        "exif_lng": exif_lng,
        "gps_source": gps_source,
        "geofence_distance_m": distance_m,
        "geofence_radius_m": radius_m,
        "within_geofence": within_geofence,
        "is_live": is_live,
        "raw_exif": meta.get("raw_exif") or None,
        "blocked_reason": blocked_reason,
        "buyer_confirmation_required": buyer_confirmation_required,
    }


def _apply_pod_verification(proof: DeliveryProof, verification: Optional[dict]) -> None:
    """Copy a computed verdict onto a proof row (or clear it for non-photo pod)."""
    if verification is None:
        proof.verification_status = None
        proof.needs_review = False
        proof.verification_note = None
        proof.buyer_confirmation_required = False
        proof.buyer_confirmed_at = None
        proof.buyer_confirmed_by = None
        return
    proof.exif_taken_at = verification.get("exif_taken_at")
    proof.exif_lat = Decimal(str(verification["exif_lat"])) if verification.get("exif_lat") is not None else None
    proof.exif_lng = Decimal(str(verification["exif_lng"])) if verification.get("exif_lng") is not None else None
    proof.gps_source = verification.get("gps_source")
    proof.geofence_distance_m = (
        Decimal(str(verification["geofence_distance_m"])) if verification.get("geofence_distance_m") is not None else None
    )
    proof.geofence_radius_m = (
        Decimal(str(verification["geofence_radius_m"])) if verification.get("geofence_radius_m") is not None else None
    )
    proof.within_geofence = verification.get("within_geofence")
    proof.is_live = bool(verification.get("is_live"))
    proof.needs_review = bool(verification.get("needs_review"))
    proof.verification_status = verification.get("verification_status")
    proof.verification_note = verification.get("verification_note")
    proof.raw_exif = verification.get("raw_exif")
    proof.buyer_confirmation_required = bool(verification.get("buyer_confirmation_required"))
    # A re-capture invalidates any earlier buyer confirmation — the new photo
    # must be confirmed afresh.
    proof.buyer_confirmed_at = None
    proof.buyer_confirmed_by = None


# ── Proof of delivery (Phase 1A) ─────────────────────────────────────

def _pod_today():
    """Today's calendar date in the market timezone used for POD confirmations.

    Falls back to UTC when the zone is unavailable so a bad config can never
    break proof capture.
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(settings.POD_BUYER_CONFIRM_TZ)).date()
    except Exception:
        return datetime.utcnow().date()


def _can_buyer_confirm(p: Optional[DeliveryProof]) -> bool:
    """Whether the buyer may confirm receipt for this proof right now.

    Only a no-GPS proof that has not yet been confirmed and whose capture day is
    *today* qualifies — so an old photo cannot be rubber-stamped days later.
    """
    if not p or not p.buyer_confirmation_required or p.buyer_confirmed_at:
        return False
    if p.exif_taken_at is None:
        return False
    return p.exif_taken_at.date() == _pod_today()


def _buyer_confirm_hint(p: Optional[DeliveryProof]) -> Optional[str]:
    """Buyer-facing reason the confirm button is available or blocked."""
    if not p or not p.buyer_confirmation_required or p.buyer_confirmed_at:
        return None
    if p.exif_taken_at is None:
        return "This proof has no capture time, so receipt cannot be confirmed."
    if p.exif_taken_at.date() != _pod_today():
        return "Confirm on the day the photo was taken; this photo is from an earlier day."
    return "This photo has no location data. Confirm you received this delivery to complete the order."


def _pod_summary(p: Optional[DeliveryProof]) -> Optional[dict]:
    """Compact proof verdict embedded on a job so driver UIs can gate the
    'mark delivered' button from server truth (survives a page reload)."""
    if not p:
        return None
    return {
        "pod_type": p.pod_type,
        "has_photo": bool(p.file_url),
        "verification_status": p.verification_status,
        "needs_review": bool(p.needs_review),
        "is_live": bool(p.is_live),
        "verification_note": p.verification_note,
        "geofence_distance_m": float(p.geofence_distance_m) if p.geofence_distance_m is not None else None,
        "exif_taken_at": p.exif_taken_at,
        "gps_source": p.gps_source,
        "captured_at": p.captured_at,
        # Buyer receipt confirmation for a no-GPS proof.
        "buyer_confirmation_required": bool(p.buyer_confirmation_required),
        "buyer_confirmed_at": p.buyer_confirmed_at,
        "buyer_can_confirm": _can_buyer_confirm(p),
        "review": {
            "status": p.review_status,
            "reviewed_at": p.reviewed_at,
        },
    }


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
        # Geotag / freshness verification (Phase 1A+)
        "exif_taken_at": p.exif_taken_at,
        "exif_lat": float(p.exif_lat) if p.exif_lat is not None else None,
        "exif_lng": float(p.exif_lng) if p.exif_lng is not None else None,
        "gps_source": p.gps_source,
        "geofence_distance_m": float(p.geofence_distance_m) if p.geofence_distance_m is not None else None,
        "geofence_radius_m": float(p.geofence_radius_m) if p.geofence_radius_m is not None else None,
        "within_geofence": p.within_geofence,
        "is_live": p.is_live,
        "needs_review": bool(p.needs_review),
        "verification_status": p.verification_status,
        "verification_note": p.verification_note,
        # Human review outcome (None until a flagged proof is reviewed)
        "review_status": p.review_status,
        "reviewed_at": p.reviewed_at,
        "review_note": p.review_note,
        # Buyer receipt confirmation for a no-GPS proof.
        "buyer_confirmation_required": bool(p.buyer_confirmation_required),
        "buyer_confirmed_at": p.buyer_confirmed_at,
        "buyer_can_confirm": _can_buyer_confirm(p),
    }


def _buyer_proof_dict(p: Optional[DeliveryProof]) -> Optional[dict]:
    """Buyer-safe projection of a proof.

    Deliberately omits raw EXIF GPS, device coords and internal review fields;
    the buyer only needs the photo, capture time and the human-readable verdict.
    """
    if not p:
        return None
    return {
        "pod_type": p.pod_type,
        "file_url": p.file_url,
        "captured_at": p.captured_at,
        "verification_status": p.verification_status,
        "verification_note": p.verification_note,
        "geofence_distance_m": float(p.geofence_distance_m) if p.geofence_distance_m is not None else None,
        "review_status": p.review_status,
        # No-GPS fallback: the buyer confirms receipt. The server decides whether
        # the button is active (capture day == today) so it can never be spoofed.
        "buyer_confirmation_required": bool(p.buyer_confirmation_required),
        "buyer_confirmed_at": p.buyer_confirmed_at,
        "can_confirm_receipt": _can_buyer_confirm(p),
        "confirm_hint": _buyer_confirm_hint(p),
    }


def _apply_pod_review(
    db: AsyncSession,
    *,
    proof: DeliveryProof,
    job: Optional[DeliveryJob],
    order_id,
    action: str,
    note: Optional[str],
    reviewer_id,
    reviewer_name: Optional[str],
    driver_user_id=None,
) -> None:
    """Record a vendor/admin review outcome on a proof (single choke point).

    `clear` accepts the proof (drops it from the review queue); `reject` keeps it
    flagged so the photo must be re-captured. Appends a buyer-visible event and
    notifies the driver. Commits nothing — the caller owns the transaction.
    """
    now = datetime.utcnow()
    if action == "clear":
        proof.review_status = "cleared"
        proof.needs_review = False
        verb = "cleared"
    else:
        proof.review_status = "rejected"
        proof.needs_review = True
        verb = "rejected"
    proof.reviewed_by = str(reviewer_id) if reviewer_id is not None else None
    proof.reviewed_at = now
    proof.review_note = (note or "").strip() or None

    _write_job_event(
        db,
        job=job,
        order_id=order_id,
        actor_type="vendor",
        actor_name=reviewer_name,
        event_type="proof_review",
        note=f"Delivery proof {verb}" + (f": {proof.review_note}" if proof.review_note else ""),
    )
    if driver_user_id is not None:
        _notify(
            db,
            driver_user_id,
            f"Proof {verb}",
            f"Delivery proof for {job.job_number if job else 'your delivery'} was {verb} by the vendor.",
        )


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
    photo_bytes: Optional[bytes] = None
    if file is not None and file.filename:
        content = await file.read()
        if not content:
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
        if len(content) > 8 * 1024 * 1024:
            raise HTTPException(http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large (max 8MB)")
        photo_bytes = content
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

    # Photo proofs are scored for freshness (hard gate) + geofence (advisory).
    # A stale/undated photo is rejected outright so the driver retakes a live one;
    # a geotag mismatch is recorded + flagged for review but still saved.
    verification: Optional[dict] = None
    if pod_type == "photo":
        verification = await _verify_pod_photo(
            db, job, content=photo_bytes, device_lat=lat, device_lng=lng
        )
        if verification.get("blocked_reason"):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=verification["blocked_reason"])

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
        _apply_pod_verification(existing, verification)
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
        _apply_pod_verification(existing, verification)
        db.add(existing)
    await db.commit()
    await db.refresh(existing)

    order_number = job.order.order_number if job.order else ""
    if job.order:
        _notify(db, job.order.user_id, "Delivery proof added",
                f"Driver {profile.full_name or profile.phone or ''} submitted proof for delivery {job.job_number} (order {order_number}).",
                "/dashboard/orders")
    # Record the capture on the buyer-facing feed, noting any review flag.
    review_note = existing.verification_note if verification is not None else None
    _write_job_event(
        db,
        job=job,
        order_id=job.order_id,
        actor_type="driver",
        actor_name=profile.full_name or profile.phone,
        event_type="proof",
        note=review_note,
    )
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


@router.post("/jobs/{job_id}/pod/review")
async def review_delivery_proof(
    job_id: UUID,
    payload: PodReviewRequest,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vendor (owner of the job) or admin records a review outcome on a proof.

    `action` is `clear` (accept) or `reject` (send back for re-capture). The
    shared helper keeps the flag, buyer-visible event and driver notification in
    sync for both this endpoint and the admin queue.
    """
    row = await db.execute(
        select(DeliveryProof, DeliveryJob)
        .join(DeliveryJob, DeliveryJob.id == DeliveryProof.delivery_job_id)
        .where(DeliveryProof.delivery_job_id == job_id)
    )
    first = row.first()
    if not first:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Proof of delivery not found for this job")
    proof, job = first

    role = current_user.role.value if current_user.role else ""
    if role not in ("admin", "super_admin"):
        if role != "vendor":
            raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Only the vendor or an admin can review this proof.")
        vrow = await db.execute(select(Vendor).where(Vendor.user_id == current_user.id))
        vendor = vrow.scalar_one_or_none()
        if not vendor or str(vendor.id) != str(job.vendor_id):
            raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Not your delivery.")

    driver_user_id = None
    if job.driver_profile_id:
        drow = await db.execute(select(DriverProfile.user_id).where(DriverProfile.id == job.driver_profile_id))
        driver_user_id = drow.scalar_one_or_none()

    reviewer_name = getattr(current_user, "email", None) or "Vendor"
    _apply_pod_review(
        db,
        proof=proof,
        job=job,
        order_id=job.order_id,
        action=payload.action,
        note=payload.note,
        reviewer_id=current_user.id,
        reviewer_name=reviewer_name,
        driver_user_id=driver_user_id,
    )
    await db.commit()
    await db.refresh(proof)
    return {"message": f"Proof {payload.action}ed", "proof": _proof_dict(proof)}


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

    Every figure is a server-side aggregate over `driver_earnings` — the same
    ledger the payout runner will read — so the portal can never disagree with
    what is actually owed:

    - `pending_balance`  net owed but not yet disbursed (status = pending)
    - `paid_total`       net already disbursed          (status = paid)
    - `total_net`        lifetime net across the full history (cancelled excluded)
    - `total_gross`      delivery fees those rows were earned on
    - `commission_total` Burncost's cut (0 while `_DRIVER_COMMISSION_RATE` is 0)

    `next_payout_date` is the first of next month and is `None` when nothing is
    pending, so the UI never advertises a payout that will not happen (same
    contract as the vendor summary in `payments.py`).

    `transactions` is the newest `_EARNINGS_TXN_LIMIT` rows; `transactions_total`
    reports the true size of the ledger so the UI can say "showing latest N".
    Payout disbursement itself runs in the Settlement & Disbursement phase.
    """
    # Only rows that still owe or have paid something count toward the totals.
    # No "cancelled" row is written today, but excluding them here means a future
    # reversal can never silently inflate a driver's balance.
    live = DriverEarnings.status != "cancelled"

    totals = (await db.execute(
        select(
            func.coalesce(func.sum(case(
                (DriverEarnings.status == "pending", DriverEarnings.net_amount), else_=0)), 0),
            func.coalesce(func.sum(case(
                (DriverEarnings.status == "paid", DriverEarnings.net_amount), else_=0)), 0),
            func.coalesce(func.sum(DriverEarnings.net_amount), 0),
            func.coalesce(func.sum(DriverEarnings.gross_amount), 0),
            func.coalesce(func.sum(DriverEarnings.commission_amount), 0),
            func.count(DriverEarnings.id),
            func.coalesce(func.sum(case(
                (DriverEarnings.status == "paid", 1), else_=0)), 0),
        ).where(DriverEarnings.driver_profile_id == profile.id, live)
    )).one()
    (
        pending_total, paid_total, net_total, gross_total,
        commission_total, txn_count, paid_count,
    ) = totals

    rows = (await db.execute(
        select(DriverEarnings, DeliveryJob)
        .outerjoin(DeliveryJob, DeliveryJob.id == DriverEarnings.delivery_job_id)
        .where(DriverEarnings.driver_profile_id == profile.id)
        .order_by(DriverEarnings.created_at.desc())
        .limit(_EARNINGS_TXN_LIMIT)
    )).all()

    # Round at the boundary: the aggregates come back as Decimal and would
    # otherwise surface binary float noise (e.g. 1234.5600000000001) in the UI.
    pending = round(float(pending_total or 0), 2)
    paid = round(float(paid_total or 0), 2)

    # Only advertise a payout when there is actually something to pay out.
    today = datetime.utcnow()
    next_payout = None
    if pending > 0:
        next_payout = (today.replace(day=1) + timedelta(days=32)).replace(day=1).date().isoformat()

    return {
        "driver_profile_id": str(profile.id),
        "full_name": profile.full_name,
        "pending_balance": pending,
        "paid_total": paid,
        "total_net": round(float(net_total or 0), 2),
        "total_gross": round(float(gross_total or 0), 2),
        "commission_total": round(float(commission_total or 0), 2),
        "commission_rate": float(_DRIVER_COMMISSION_RATE),
        "completed_count": int(txn_count or 0),
        "paid_count": int(paid_count or 0),
        "next_payout_date": next_payout,
        "transactions_total": int(txn_count or 0),
        "transactions_limit": _EARNINGS_TXN_LIMIT,
        # Payout destination, so the earnings surface can show/collect the
        # settlement account without a second round-trip.
        "payout_account": {
            "has_bank_details": profile.has_bank_details,
            "bank_name": profile.bank_name,
            # Only the last 4 digits ever leave the server for display; the full
            # number stays on the profile (editable via PATCH /drivers/me/bank-details).
            "account_number_masked": (
                f"••••{profile.bank_account_number[-4:]}"
                if profile.bank_account_number and len(profile.bank_account_number) >= 4
                else None
            ),
            "account_name": profile.bank_account_name,
            "bank_code": profile.bank_code,
        },
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

    # Optional manual status transition / note piggy-backed on the heartbeat.
    # When neither is supplied the endpoint behaves exactly as before (pure GPS).
    if job and (payload.status or payload.note is not None):
        if payload.status:
            await _driver_transition(db, profile, job.id, payload.status)
        if payload.note is not None:
            _write_job_event(
                db,
                job=job,
                order_id=job.order_id,
                actor_type="driver",
                actor_name=profile.full_name or profile.phone,
                event_type="note",
                note=payload.note,
            )

            # The feed is only visible from the order page, so the same note
            # also lands in the buyer's notification bell.
            if job.order_id:
                order = (
                    await db.execute(select(Order).where(Order.id == job.order_id))
                ).scalar_one_or_none()
                _notify_buyer_of_note(
                    db,
                    buyer_id=order.user_id if order is not None else None,
                    order_number=order.order_number if order is not None else None,
                    note=payload.note,
                    actor_name=profile.full_name or profile.phone,
                )

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

    # Fan the beat out to any buyer/vendor watching this job over SSE. Best
    # effort only: a Redis outage must never fail a location heartbeat (the
    # client's polling fallback covers that case).
    if job:
        await publish_job_location(job.id, {
            "job_id": str(job.id),
            "lat": float(payload.lat),
            "lng": float(payload.lng),
            "accuracy": payload.accuracy,
            "status": job.status,
            "recorded_at": now.isoformat(),
        })
    return resp


@router.get("/jobs/{job_id}/track")
async def track_job(
    job_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One-shot tracking snapshot (the polling / SSE-fallback contract)."""
    return await _job_tracking_payload(db, job_id, current_user)


async def _job_tracking_payload(db: AsyncSession, job_id: UUID, current_user) -> dict:
    """Authorize the caller and build the live-tracking payload for a job.

    Shared by `/track` (polling), `/stream` (SSE snapshot) and `/trail` so every
    surface enforces exactly the same ownership rules.
    """
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

    job_events = await _job_events(db, job.id)
    proof = await _load_job_proof(db, job.id)

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
        # Breadcrumb trail (oldest first) for the polyline on the live map.
        "trail": await _job_trail(db, job.id),
        # Manual update feed (driver/vendor), shown alongside the live map.
        "events": job_events,
        # Buyer-safe proof (photo + verdict) + compact gate summary.
        "proof": _buyer_proof_dict(proof),
        "pod": _pod_summary(proof),
    }


async def _job_trail(db: AsyncSession, job_id, limit: int = 200) -> list:
    """Last `limit` recorded positions for a job, oldest first (map polyline).

    `driver_location_updates` is the time-series feed the driver heartbeat
    already writes, so the breadcrumb trail needs no extra storage.
    """
    rows = (
        await db.execute(
            select(DriverLocationUpdate)
            .where(DriverLocationUpdate.delivery_job_id == job_id)
            .order_by(DriverLocationUpdate.recorded_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {"lat": float(r.lat), "lng": float(r.lng), "recorded_at": r.recorded_at}
        for r in reversed(rows)
    ]


@router.get("/jobs/{job_id}/trail")
async def job_trail(
    job_id: UUID,
    limit: int = 200,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The recorded position breadcrumbs for one job (oldest first)."""
    await _job_tracking_payload(db, job_id, current_user)  # ownership check
    return {"job_id": str(job_id), "points": await _job_trail(db, job_id, max(1, min(limit, 500)))}


@router.get("/jobs/{job_id}/stream")
async def stream_job_tracking(
    job_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Server-sent events carrying a job's live position (buyer/vendor/driver).

    Emits an initial `snapshot` event (the same payload as `/track`) so the map
    paints immediately, then one `location` event per driver heartbeat, plus a
    keep-alive comment while idle. The stream closes after ~15 minutes and the
    browser reconnects on its own; clients keep a 5s polling fallback so a
    closed or blocked stream never leaves the map stale.
    """
    snapshot = await _job_tracking_payload(db, job_id, current_user)

    async def event_stream():
        # `retry:` asks the browser to back off before reconnecting.
        yield "retry: 10000\n\n"
        yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"
        async for payload in iter_job_locations(job_id):
            if payload is None:
                yield ": keep-alive\n\n"
            else:
                yield f"event: location\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tell nginx (and friends) not to buffer the stream.
            "X-Accel-Buffering": "no",
        },
    )



async def _job_events(db: AsyncSession, job_id) -> list:
    """Last ~20 manual updates for a job, newest first (buyer-facing feed)."""
    rows = (
        await db.execute(
            select(DeliveryJobEvent)
            .where(DeliveryJobEvent.delivery_job_id == job_id)
            .order_by(DeliveryJobEvent.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    return [
        {
            "actor_type": e.actor_type,
            "actor_name": e.actor_name,
            "event_type": e.event_type,
            "status": e.status,
            "lat": float(e.lat) if e.lat is not None else None,
            "lng": float(e.lng) if e.lng is not None else None,
            "note": e.note,
            "created_at": e.created_at,
        }
        for e in rows
    ]


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
    proof_rows = (
        await db.execute(
            select(DeliveryProof).where(DeliveryProof.delivery_job_id.in_([j.id for j in jobs] or [None]))
        )
    ).scalars().all()
    proofs = _load_proofs_map(proof_rows)
    return {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "jobs": [
            _job_dict(j, order_number=order.order_number, proof=proofs.get(j.id))
            for j in jobs
        ],
    }