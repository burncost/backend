"""User-facing demand alert endpoints (Phase 4)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from datetime import datetime
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.vendor import Vendor
from app.models.demand_alert import DemandAlert, demand_alert_cutoff
from app.services import demand_broadcast_service as demand_broadcast

router = APIRouter()


@router.post("/")
async def create_demand_alert(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Builder posts an item requirement (demand alert)."""
    item_description = payload.get("item_description")
    city = payload.get("city")
    if not item_description or not city:
        raise HTTPException(400, "item_description and city required")
    alert = DemandAlert(
        item_description=item_description,
        city=city,
        quantity_needed=payload.get("quantity_needed"),
        unit=payload.get("unit"),
        project_title=payload.get("project_title"),
        requested_by=current_user.id,
        status="pending",
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return {"id": str(alert.id), "status": alert.status, "city": alert.city}


@router.get("/")
async def list_demand_alerts(
    city: str = None,
    db: AsyncSession = Depends(get_db),
):
    """List demand alerts (optionally filtered by city).

    Alerts expire 48h after being raised, so older ones are never returned.
    Admins can also close a broadcast from the console, which hides it here.
    """
    query = (
        select(DemandAlert)
        .where(DemandAlert.created_at >= demand_alert_cutoff())
        .where(or_(DemandAlert.status.is_(None), DemandAlert.status != "closed"))
        .order_by(DemandAlert.created_at.desc())
    )
    if city:
        # Case/whitespace-insensitive so "Abuja" matches a vendor's stored "abuja ".
        query = query.where(func.lower(func.trim(DemandAlert.city)) == func.lower(city.strip()))
    rows = (await db.execute(query)).scalars().all()
    return {"alerts": [
        {
            "id": str(a.id),
            "item_description": a.item_description,
            "city": a.city,
            "quantity_needed": float(a.quantity_needed) if a.quantity_needed is not None else None,
            "unit": a.unit,
            "project_title": a.project_title,
            "status": a.status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in rows
    ]}


@router.get("/{alert_id}/bids")
async def list_demand_alert_bids(
    alert_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Per-vendor bid threads opened against a demand alert (public read)."""
    alert = (await db.execute(
        select(DemandAlert).where(DemandAlert.id == alert_id)
    )).scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Demand alert not found")
    bids = await demand_broadcast.list_alert_bids(db, alert_id)
    return {
        "alert_id": str(alert_id),
        "item_description": alert.item_description,
        "bids": bids,
    }


@router.post("/{alert_id}/bid")
async def place_demand_alert_bid(
    alert_id: UUID,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vendor places a bid on a demand alert.

    Guarded so only a vendor that actually sells a matching product can bid -
    otherwise the demand feed becomes a cold-call channel.
    """
    vendor = (await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )).scalar_one_or_none()
    if not vendor:
        raise HTTPException(404, "Vendor profile not found")

    alert = (await db.execute(
        select(DemandAlert).where(DemandAlert.id == alert_id)
    )).scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Demand alert not found")
    if (alert.status or "") == "closed":
        raise HTTPException(400, "This demand request has been closed")

    bid_price = payload.get("bid_price")
    try:
        bid_price = float(bid_price)
    except (TypeError, ValueError):
        raise HTTPException(400, "A numeric bid_price is required")
    if bid_price <= 0:
        raise HTTPException(400, "bid_price must be greater than zero")

    if not await demand_broadcast.vendor_sells_matching_product(
        db, vendor.id, alert.item_description
    ):
        raise HTTPException(403, "You do not sell a matching product for this demand")

    try:
        negotiation = await demand_broadcast.place_vendor_bid(
            db, alert, vendor,
            bid_price=bid_price,
            note=payload.get("note", ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    return {
        "negotiation_id": str(negotiation.id),
        "negotiation_number": negotiation.negotiation_number,
        "bid_price": float(negotiation.bid_price or 0),
        "status": negotiation.status,
    }