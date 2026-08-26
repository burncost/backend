"""User-facing demand alert endpoints (Phase 4)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.demand_alert import DemandAlert

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
    """List demand alerts (optionally filtered by city)."""
    query = select(DemandAlert).order_by(DemandAlert.created_at.desc())
    if city:
        query = query.where(DemandAlert.city == city)
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