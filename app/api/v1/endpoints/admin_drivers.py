"""Admin driver & delivery console endpoints (Phase 6).

Admin-tier users (via require_roles) can list drivers, change a driver's
account status (active/suspended/pending) and inspect a driver's delivery jobs.

These live under /api/v1/admin/drivers/* and are mounted with the other admin
routers (prefix "/admin"). Uses the standard RBAC guard factory.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.database import get_db
from app.models.delivery import DeliveryJob
from app.models.driver import DriverProfile
from app.models.order import Order
from app.models.user import User
from app.models.vendor import Vendor

logger = logging.getLogger(__name__)
router = APIRouter()

admin_guard = require_roles("manager", "support", "marketing")

_ALLOWED_STATUS = {"active", "suspended", "pending"}


class DriverStatusUpdate(BaseModel):
    status: str


def _driver_payload(driver: DriverProfile, user: Optional[User], vendor: Optional[Vendor]) -> dict:
    return {
        "id": str(driver.id),
        "user_id": str(driver.user_id),
        "email": user.email if user else None,
        "full_name": driver.full_name,
        "phone": driver.phone,
        "source": driver.source,
        "status": driver.status,
        "availability": bool(driver.availability),
        "vehicle_type": driver.vehicle_type,
        "vehicle_plate": driver.vehicle_plate,
        "license_no": driver.license_no,
        "default_city": driver.default_city,
        "default_state": driver.default_state,
        "rating": float(driver.rating) if driver.rating is not None else None,
        "rating_count": driver.rating_count or 0,
        "vendor_name": vendor.business_name if vendor else None,
        "created_at": driver.created_at,
    }


async def _load_driver_with_relations(db: AsyncSession, driver_id: UUID):
    result = await db.execute(
        select(DriverProfile, User, Vendor)
        .join(User, User.id == DriverProfile.user_id)
        .outerjoin(Vendor, Vendor.id == DriverProfile.vendor_id)
        .where(DriverProfile.id == driver_id)
    )
    return result.first()


@router.get("/drivers")
async def list_drivers(
    status: Optional[str] = Query(None, pattern="^(active|suspended|pending)$"),
    availability: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    _: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(DriverProfile, User, Vendor)
        .join(User, User.id == DriverProfile.user_id)
        .outerjoin(Vendor, Vendor.id == DriverProfile.vendor_id)
    )
    if status:
        q = q.where(DriverProfile.status == status)
    if availability is not None:
        q = q.where(DriverProfile.availability == availability)
    if search:
        term = f"%{search.strip()}%"
        q = q.where(
            or_(
                User.email.ilike(term),
                DriverProfile.full_name.ilike(term),
                DriverProfile.phone.ilike(term),
            )
        )
    q = q.order_by(DriverProfile.created_at.desc())
    result = await db.execute(q)
    rows = result.all()
    drivers = [_driver_payload(d, u, v) for d, u, v in rows]
    return {"total": len(drivers), "drivers": drivers}


@router.patch("/drivers/{driver_id}/status")
async def update_driver_status(
    driver_id: UUID,
    payload: DriverStatusUpdate,
    _: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    if payload.status not in _ALLOWED_STATUS:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail="Status must be one of: active, suspended, pending.",
        )
    result = await db.execute(select(DriverProfile).where(DriverProfile.id == driver_id))
    driver = result.scalar_one_or_none()
    if not driver:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Driver not found")
    driver.status = payload.status
    if payload.status == "suspended":
        driver.availability = False
    await db.commit()
    row = await _load_driver_with_relations(db, driver_id)
    if not row:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Driver not found")
    d, u, v = row
    return _driver_payload(d, u, v)


@router.get("/drivers/{driver_id}/dispatches")
async def list_driver_dispatches(
    driver_id: UUID,
    _: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DeliveryJob, Order)
        .join(Order, Order.id == DeliveryJob.order_id)
        .where(DeliveryJob.driver_profile_id == driver_id)
        .order_by(DeliveryJob.created_at.desc())
    )
    rows = result.all()
    dispatches = []
    for job, order in rows:
        dispatches.append({
            "id": str(job.id),
            "job_number": job.job_number,
            "order_number": order.order_number,
            "status": job.status,
            "assignment_type": job.assignment_type,
            "driver_name": job.driver_name,
            "driver_phone": job.driver_phone,
            "dropoff_city": job.dropoff_city,
            "dropoff_state": job.dropoff_state,
            "delivery_fee": float(job.delivery_fee or 0),
            "accepted_at": job.accepted_at,
            "picked_up_at": job.picked_up_at,
            "delivered_at": job.delivered_at,
            "cancelled_at": job.cancelled_at,
            "created_at": job.created_at,
        })
    return {"dispatches": dispatches}