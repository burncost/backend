"""Phase 2 admin negotiation endpoints — Negotiation Center, Discount Config,
Supplier Performance, Builder Activity, and Negotiation Analytics.

These power the negotiation admin pages. Powered by the new negotiation models.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from uuid import UUID
from typing import Optional

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.user import User, UserProfile, UserRole
from app.models.vendor import Vendor
from app.models.negotiation import (
    Negotiation,
    NegotiationCounterOffer,
    DiscountConfiguration,
    NegotiationAuditEntry,
)
from app.models.order import Order, OrderItem
from app.models.product import Product

router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")


def _status_label(s: str) -> str:
    """Convert snake_case DB status to display label (e.g. counter_accepted -> Counter Accepted)."""
    if not s:
        return s
    return s.replace("_", " ").title()


def _neg_dict(n: Negotiation) -> dict:
    builder_name = n.builder.email if n.builder else "Unknown"
    if n.builder and n.builder.profile:
        builder_name = f"{n.builder.profile.first_name} {n.builder.profile.last_name}".strip() or builder_name
    return {
        "id": str(n.id),
        "negotiation_number": n.negotiation_number,
        "builder": builder_name,
        "supplier": n.supplier.business_name if n.supplier else "Unknown",
        "product": n.product_name,
        "category": n.category,
        "quantity": float(n.quantity or 0),
        "unit": n.unit,
        "requested_discount": float(n.requested_discount or 0),
        "counter_offer": float(n.counter_offer) if n.counter_offer is not None else None,
        "final_discount": float(n.final_discount) if n.final_discount is not None else None,
        "value": float(n.value or 0),
        "status": _status_label(n.status),
        "flagged": bool(n.flagged),
        "suspended": bool(n.suspended),
        "request_date": n.created_at.strftime("%Y-%m-%d") if n.created_at else "—",
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "counter_offers": [
            {"id": str(c.id), "offered_by": c.offered_by,
             "discount_percent": float(c.discount_percent or 0),
             "note": c.note, "created_at": c.created_at.isoformat() if c.created_at else None}
            for c in n.counter_offers
        ],
    }


@router.get("/negotiations")
async def admin_list_negotiations(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """All negotiations for the center, with optional status/search filtering."""
    query = (select(Negotiation)
             .options(selectinload(Negotiation.builder).selectinload(User.profile),
                      selectinload(Negotiation.supplier),
                      selectinload(Negotiation.counter_offers)))
    count_query = select(func.count(Negotiation.id))

    if status and status != "All":
        q = status.lower()
        if q == "auto approved":
            q = "auto_approved"
        elif q == "auto rejected":
            q = "auto_rejected"
        elif q == "counter offered":
            q = "counter_offered"
        elif q == "counter accepted":
            q = "counter_accepted"
        elif q == "counter declined":
            q = "counter_declined"
        else:
            q = status.lower().replace(" ", "_")
        query = query.where(Negotiation.status == q)
        count_query = count_query.where(Negotiation.status == q)
    if search:
        like = f"%{search}%"
        query = query.where(Negotiation.negotiation_number.ilike(like))
        count_query = count_query.where(Negotiation.negotiation_number.ilike(like))

    total = (await db.execute(count_query)).scalar() or 0
    rows = (await db.execute(query.order_by(Negotiation.created_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return {
        "negotiations": [_neg_dict(n) for n in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


class _Summary:
    """Helper to compute the negotiation summary card numbers."""

    @staticmethod
    async def compute(db) -> dict:
        active = (await db.execute(select(func.count(Negotiation.id)).where(
            Negotiation.status.in_(["pending", "counter_offered"])))).scalar() or 0
        pending = (await db.execute(select(func.count(Negotiation.id)).where(
            Negotiation.status == "pending"))).scalar() or 0
        total = (await db.execute(select(func.count(Negotiation.id)))).scalar() or 0
        approved = (await db.execute(select(func.count(Negotiation.id)).where(
            Negotiation.status.in_(["approved", "auto_approved", "counter_accepted"])))).scalar() or 0
        rejected = (await db.execute(select(func.count(Negotiation.id)).where(
            Negotiation.status.in_(["rejected", "auto_rejected", "counter_declined"])))).scalar() or 0
        success_rate = round((approved / total) * 100, 1) if total else 0
        return {
            "total_requests": str(total),
            "active_negotiations": str(active),
            "pending_review": str(pending),
            "success_rate": f"{success_rate}%",
            "approved_requests": str(approved),
            "rejected_requests": str(rejected),
        }


@router.get("/negotiations/summary")
async def admin_negotiation_summary(
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Summary card numbers for the Negotiation Center."""
    return await _Summary.compute(db)


@router.get("/negotiations/{negotiation_id}")
async def admin_negotiation_detail(
    negotiation_id: UUID,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """A single negotiation with counter offers and audit entries."""
    n = (await db.execute(select(Negotiation)
                          .options(selectinload(Negotiation.builder).selectinload(User.profile),
                                   selectinload(Negotiation.supplier),
                                   selectinload(Negotiation.counter_offers),
                                   selectinload(Negotiation.audit_entries))
                          .where(Negotiation.id == negotiation_id))).scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Negotiation not found")
    return _neg_dict(n)


@router.post("/negotiations/{negotiation_id}/review")
async def admin_negotiation_review(
    negotiation_id: UUID,
    payload: dict,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Admin action: force approve/reject/flag/suspend or send counter."""
    action = (payload or {}).get("action") or (payload or {}).get("type")
    note = (payload or {}).get("note", "")
    if not action:
        raise HTTPException(400, "action is required")

    n = (await db.execute(select(Negotiation).where(Negotiation.id == negotiation_id))).scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Negotiation not found")

    prev = n.status
    new = n.status
    if action in ("approve", "force_approve"):
        n.status = "approved"
        n.final_discount = n.requested_discount
        new = "approved"
    elif action in ("reject", "force_reject"):
        n.status = "rejected"
        new = "rejected"
    elif action == "flag":
        n.flagged = True
    elif action == "unflag":
        n.flagged = False
    elif action == "suspend":
        n.suspended = True
    elif action == "unsuspend":
        n.suspended = False
    else:
        raise HTTPException(400, f"Unknown action: {action}")

    if note:
        n.admin_note = note
    await db.commit()

    # Audit entry
    db.add(NegotiationAuditEntry(
        negotiation_id=n.id,
        action=f"Admin: {action}",
        performed_by=str(current_user.get("id", "admin")),
        prev_value=prev,
        new_value=new,
        note=note,
    ))
    await db.commit()

    return {"negotiation_id": str(n.id), "status": n.status, "flagged": n.flagged, "suspended": n.suspended}


@router.get("/discount-configs")
async def admin_list_discount_configs(current_user=Depends(admin_guard), db=Depends(get_db)):
    rows = (await db.execute(select(DiscountConfiguration).options(selectinload(DiscountConfiguration.supplier)))).scalars().all()
    return {"configs": [
        {"id": str(c.id), "config_number": c.config_number,
         "supplier": c.supplier.business_name if c.supplier else "Unknown",
         "product": c.product_name, "category": c.category,
         "discount_enabled": bool(c.discount_enabled),
         "max_discount_pct": float(c.max_discount_pct or 0),
         "auto_approval_threshold": float(c.auto_approval_threshold or 0),
         "auto_rejection_threshold": float(c.auto_rejection_threshold or 0),
         "min_order_qty": c.min_order_qty or 0, "min_order_value": float(c.min_order_value or 0),
         "quote_expiration": c.quote_expiration_hours or 0,
         "last_modified": c.updated_at.strftime("%Y-%m-%d") if c.updated_at else "—",
         "modified_by": c.last_modified_by or "System"}
        for c in rows]}


@router.patch("/discount-configs/{config_id}")
async def admin_update_discount_config(config_id: UUID, payload: dict, current_user=Depends(admin_guard), db=Depends(get_db)):
    c = (await db.execute(select(DiscountConfiguration).where(DiscountConfiguration.id == config_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Config not found")
    for key in ("max_discount_pct", "auto_approval_threshold", "auto_rejection_threshold",
                "min_order_qty", "min_order_value", "quote_expiration_hours", "discount_enabled"):
        if key in payload and payload[key] is not None:
            setattr(c, key, payload[key])
    c.last_modified_by = "Admin"
    await db.commit()
    return {"id": str(c.id), "status": "updated"}


@router.get("/negotiation/analytics")
async def admin_negotiation_analytics(current_user=Depends(admin_guard), db=Depends(get_db)):
    """Aggregated analytics for negotiation performance."""
    total = (await db.execute(select(func.count(Negotiation.id)))).scalar() or 0
    approved = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.status.in_(["approved", "auto_approved", "counter_accepted"])))).scalar() or 0
    rejected = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.status.in_(["rejected", "auto_rejected", "counter_declined"])))).scalar() or 0
    pending = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.status == "pending"))).scalar() or 0
    counter = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.status == "counter_offered"))).scalar() or 0
    savings = (await db.execute(select(func.coalesce(func.sum(Order.discount_amount), 0)))).scalar() or 0
    success_rate = round((approved / total) * 100, 1) if total else 0

    now = datetime.utcnow()
    monthly = []
    for i in range(5, -1, -1):
        start = (now - timedelta(days=i * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (now - timedelta(days=(i - 1) * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        reqs = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.created_at >= start, Negotiation.created_at < end))).scalar() or 0
        app = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.created_at >= start, Negotiation.created_at < end, Negotiation.status.in_(["approved", "auto_approved", "counter_accepted"])))).scalar() or 0
        rej = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.created_at >= start, Negotiation.created_at < end, Negotiation.status.in_(["rejected", "auto_rejected", "counter_declined"])))).scalar() or 0
        co = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.created_at >= start, Negotiation.created_at < end, Negotiation.status == "counter_offered"))).scalar() or 0
        monthly.append({"month": start.strftime("%b"), "requests": reqs, "approved": app, "rejected": rej, "counterOffers": co})

    cats = (await db.execute(select(Negotiation.category, func.count(Negotiation.id)).group_by(Negotiation.category).order_by(func.count(Negotiation.id).desc()).limit(6))).all()
    total_cat = sum(r[1] for r in cats) or 1

    return {
        "total_requests": str(total), "approved_requests": str(approved),
        "rejected_requests": str(rejected), "pending_requests": str(pending),
        "counter_offers": str(counter), "success_rate": f"{success_rate}%",
        "total_savings": float(savings), "avg_requested_discount": "—", "avg_approved_discount": "—",
        "monthly_trend": monthly,
        "category_breakdown": [{"name": r[0] or "Other", "value": round((r[1] / total_cat) * 100)} for r in cats],
        "funnel": [
            {"name": "Requests Created", "value": total, "fill": "#1A3C6B"},
            {"name": "Pending / Review", "value": pending + counter, "fill": "#F5881F"},
            {"name": "Approved", "value": approved, "fill": "#1E7E34"},
        ],
    }


@router.get("/negotiation/supplier-performance")
async def admin_supplier_performance(current_user=Depends(admin_guard), db=Depends(get_db)):
    """Per-supplier negotiation performance scorecards."""
    rows = (await db.execute(select(Negotiation.supplier_id, func.count(Negotiation.id)).group_by(Negotiation.supplier_id))).all()
    vendors_map = {str(v.id): v for v in (await db.execute(select(Vendor))).scalars().all()}
    scores = []
    for sid, count in rows:
        v = vendors_map.get(str(sid))
        approved = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.supplier_id == sid, Negotiation.status.in_(["approved", "auto_approved", "counter_accepted"])))).scalar() or 0
        success_rate = round((approved / count) * 100) if count else 0
        scores.append({
            "supplier": v.business_name if v else str(sid), "total_requests": count,
            "approval_rate": success_rate, "rejection_rate": 100 - success_rate,
            "avg_response_time": 0, "counter_offer_rate": 0, "success_rate": success_rate,
            "risk_level": "Low" if success_rate >= 70 else "Medium" if success_rate >= 55 else "High",
            "flags": [],
        })
    scores.sort(key=lambda x: x["total_requests"], reverse=True)
    return {"scorecards": scores}


@router.get("/negotiation/builder-activity")
async def admin_builder_activity(current_user=Depends(admin_guard), db=Depends(get_db)):
    """Per-builder negotiation activity / risk flags."""
    rows = (await db.execute(select(Negotiation.builder_id, func.count(Negotiation.id), func.coalesce(func.avg(Negotiation.requested_discount), 0)).group_by(Negotiation.builder_id))).all()
    users_map = {str(u.id): u for u in (await db.execute(select(User).options(selectinload(User.profile)))).scalars().all()}
    builders = []
    for bid, count, avg_req in rows:
        u = users_map.get(str(bid))
        name = u.email if u else str(bid)
        if u and u.profile:
            name = f"{u.profile.first_name} {u.profile.last_name}".strip() or name
        approved = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.builder_id == bid, Negotiation.status.in_(["approved", "auto_approved", "counter_accepted"])))).scalar() or 0
        declined = (await db.execute(select(func.count(Negotiation.id)).where(Negotiation.builder_id == bid, Negotiation.status.in_(["rejected", "auto_rejected", "counter_declined"])))).scalar() or 0
        success_rate = round((approved / count) * 100) if count else 0
        risk = "Normal"
        flags = []
        if float(avg_req or 0) > 15:
            risk = "Flagged"
            flags.append("Spam Requests")
        elif count > 10 and success_rate < 50:
            risk = "Watch"
            flags.append("Potential Abuse")
        builders.append({
            "builder": name, "total_requests": count,
            "avg_discount_requested": round(float(avg_req or 0), 1),
            "success_rate": success_rate, "accepted_quotes": approved,
            "declined_quotes": declined, "expired_quotes": 0,
            "top_categories": [], "request_frequency": 0,
            "risk_level": risk, "flags": flags,
        })
    builders.sort(key=lambda x: x["total_requests"], reverse=True)
    return {"builders": builders}
