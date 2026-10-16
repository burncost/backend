"""Admin demand-broadcast oversight endpoints.

BOQ-triggered demand broadcasts reuse `DemandAlert` (the public line) plus
`Negotiation` rows with `demand_alert_id` set (one per-vendor bid thread).
Those bid threads are deliberately excluded from the discount tooling in
`admin_negotiation.py`, so they are surfaced here instead — read-only browsing
plus a close/reopen moderation action.

No new tables and no migration: this module only reads existing columns.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_roles
from app.core.database import get_db
from app.models.demand_alert import DEMAND_ALERT_TTL_HOURS, DemandAlert, demand_alert_cutoff
from app.models.negotiation import Negotiation
from app.models.user import User
from app.models.vendor import Vendor
from app.services import demand_broadcast_service as demand_broadcast

router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")

# "closed" means ops closed this broadcast: hidden from the public feed and no
# longer biddable. Anything else counts as open.
CLOSED_STATUS = "closed"
OPEN_STATUS = "pending"


def _broadcast_dict(alert: DemandAlert, builder_name: Optional[str] = None, **extra) -> dict:
    created = alert.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    status = alert.status or OPEN_STATUS
    return {
        "id": str(alert.id),
        "item_description": alert.item_description,
        "city": alert.city,
        "quantity_needed": float(alert.quantity_needed) if alert.quantity_needed is not None else None,
        "unit": alert.unit,
        "project_title": alert.project_title,
        "status": status,
        "is_closed": status == CLOSED_STATUS,
        "is_expired": bool(created and created < demand_alert_cutoff()),
        "expires_at": (created + timedelta(hours=DEMAND_ALERT_TTL_HOURS)).isoformat() if created else None,
        "builder": builder_name or "Unknown",
        "requested_by": str(alert.requested_by) if alert.requested_by else None,
        "created_at": created.isoformat() if created else None,
        "updated_at": alert.updated_at.isoformat() if alert.updated_at else None,
        **extra,
    }


async def _builder_names(db: AsyncSession, ids) -> dict:
    """Map `requested_by` user id -> display name for the given ids."""
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    users = (await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id.in_(ids))
    )).scalars().all()
    names = {}
    for u in users:
        name = u.email
        if u.profile:
            name = f"{u.profile.first_name or ''} {u.profile.last_name or ''}".strip() or name
        names[str(u.id)] = name
    return names


@router.get("/demand-broadcasts/summary")
async def admin_demand_broadcast_summary(
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Card numbers for the Demand Broadcasts page. Expired rows are included."""
    cutoff = demand_alert_cutoff()
    total = (await db.execute(select(func.count(DemandAlert.id)))).scalar() or 0
    closed = (await db.execute(select(func.count(DemandAlert.id)).where(
        DemandAlert.status == CLOSED_STATUS))).scalar() or 0
    expired = (await db.execute(select(func.count(DemandAlert.id)).where(
        DemandAlert.status != CLOSED_STATUS, DemandAlert.created_at < cutoff))).scalar() or 0
    total_bids = (await db.execute(select(func.count(Negotiation.id)).where(
        Negotiation.demand_alert_id.isnot(None)))).scalar() or 0
    with_bids = (await db.execute(select(func.count(func.distinct(Negotiation.demand_alert_id))).where(
        Negotiation.demand_alert_id.isnot(None)))).scalar() or 0
    conversion_rate = round((with_bids / total) * 100, 1) if total else 0
    return {
        "total_broadcasts": str(total),
        "active_broadcasts": str(total - closed - expired),
        "closed_broadcasts": str(closed),
        "expired_broadcasts": str(expired),
        "total_bids": str(total_bids),
        "broadcasts_with_bids": str(with_bids),
        "conversion_rate": f"{conversion_rate}%",
    }


@router.get("/demand-broadcasts")
async def admin_list_demand_broadcasts(
    city: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    include_expired: bool = Query(True),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """All demand broadcasts — admins see history, so expired rows are included."""
    query = select(DemandAlert)
    count_query = select(func.count(DemandAlert.id))

    # `include_expired=False` narrows to rows still inside the public 48h window.
    if not include_expired:
        live = DemandAlert.created_at >= demand_alert_cutoff()
        query = query.where(live)
        count_query = count_query.where(live)
    if city and city != "All":
        # Same case/whitespace-insensitive comparison as the public feed.
        match = func.lower(func.trim(DemandAlert.city)) == func.lower(city.strip())
        query = query.where(match)
        count_query = count_query.where(match)
    if status and status != "All":
        q = status.lower()
        if q == "active":
            cond = (DemandAlert.status != CLOSED_STATUS,
                    DemandAlert.created_at >= demand_alert_cutoff())
        elif q == "expired":
            cond = (DemandAlert.status != CLOSED_STATUS,
                    DemandAlert.created_at < demand_alert_cutoff())
        else:
            cond = (DemandAlert.status == q,)
        query = query.where(*cond)
        count_query = count_query.where(*cond)
    if search:
        like = f"%{search}%"
        match = or_(DemandAlert.item_description.ilike(like),
                    DemandAlert.project_title.ilike(like),
                    DemandAlert.city.ilike(like))
        query = query.where(match)
        count_query = count_query.where(match)

    total = (await db.execute(count_query)).scalar() or 0
    rows = (await db.execute(query.order_by(DemandAlert.created_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()

    # Bid aggregates for this page only — one extra query, no N+1.
    alert_ids = [a.id for a in rows]
    bids_by_alert: dict = {}
    if alert_ids:
        bid_rows = (await db.execute(
            select(Negotiation.demand_alert_id, Negotiation.bid_price,
                   Negotiation.created_at, Vendor.business_name)
            .join(Vendor, Vendor.id == Negotiation.supplier_id, isouter=True)
            .where(Negotiation.demand_alert_id.in_(alert_ids))
            .order_by(Negotiation.created_at.asc())
        )).all()
        for alert_id, bid_price, created_at, vendor_name in bid_rows:
            bids_by_alert.setdefault(alert_id, []).append((bid_price, created_at, vendor_name))

    def _aggregates(alert_id) -> dict:
        bids = bids_by_alert.get(alert_id, [])
        priced = [b for b in bids if b[0] is not None]
        best = min(priced, key=lambda b: b[0]) if priced else None
        return {
            "bid_count": len(bids),
            "best_bid": float(best[0]) if best else None,
            "best_vendor": best[2] if best else None,
            "last_bid_at": bids[-1][1].isoformat() if bids and bids[-1][1] else None,
        }

    names = await _builder_names(db, [a.requested_by for a in rows])
    cities = [c[0] for c in (await db.execute(
        select(func.distinct(DemandAlert.city)).order_by(DemandAlert.city))).all() if c[0]]

    return {
        "broadcasts": [
            _broadcast_dict(a, names.get(str(a.requested_by)), **_aggregates(a.id))
            for a in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "cities": cities,
    }


@router.get("/demand-broadcasts/{alert_id}")
async def admin_demand_broadcast_detail(
    alert_id: UUID,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """One broadcast with every per-vendor bid thread and its message log."""
    alert = (await db.execute(select(DemandAlert).where(DemandAlert.id == alert_id))).scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Demand broadcast not found")
    names = await _builder_names(db, [alert.requested_by])
    bids = await demand_broadcast.list_alert_bids(db, alert_id)
    priced = [b for b in bids if b.get("bid_price") is not None]
    best = min(priced, key=lambda b: b["bid_price"]) if priced else None
    return {
        **_broadcast_dict(alert, names.get(str(alert.requested_by)),
                          bid_count=len(bids),
                          best_bid=best["bid_price"] if best else None,
                          best_vendor=best["supplier"] if best else None,
                          last_bid_at=bids[-1]["created_at"] if bids else None),
        "bids": bids,
    }


@router.post("/demand-broadcasts/{alert_id}/status")
async def admin_demand_broadcast_status(
    alert_id: UUID,
    payload: dict,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Moderation: close a broadcast (hide + stop bids) or reopen it."""
    action = (payload or {}).get("action")
    if action not in ("close", "reopen"):
        raise HTTPException(400, "action must be 'close' or 'reopen'")

    alert = (await db.execute(select(DemandAlert).where(DemandAlert.id == alert_id))).scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Demand broadcast not found")

    alert.status = CLOSED_STATUS if action == "close" else OPEN_STATUS
    alert.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(alert)
    return {
        "id": str(alert.id),
        "status": alert.status,
        "is_closed": alert.status == CLOSED_STATUS,
        "item_description": alert.item_description,
    }

