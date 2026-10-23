"""Phase 5 admin dispute endpoints - Disputes, Details, Resolution.

Powered by the new dispute models.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from uuid import UUID
from typing import Optional

from app.config import settings
from app.core.database import get_db
from app.api.deps import require_roles
from app.models.dispute import Dispute, DisputeEvidence, DisputeResolution, DisputeTimeline
from app.models.order import Order
from app.models.refund import Refund
from app.services import escrow_service, refund_service

router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")


def _dispute_dict(d: Dispute) -> dict:
    return {
        "id": str(d.id),
        "dispute_number": d.dispute_number,
        "dispute_type": d.dispute_type,
        "status": d.status,
        "priority": d.priority,
        "description": d.description,
        "buyer": d.buyer_name,
        "supplier": d.supplier_name,
        "order_number": d.order_number,
        "amount": float(d.amount or 0),
        "filed_by": d.filed_by,
        "filed_at": d.filed_at.isoformat() if d.filed_at else None,
        "created_date": d.filed_at.strftime("%b %d, %Y") if d.filed_at else "-",
        "evidence": [
            {"id": str(e.id), "submitted_by": e.submitted_by, "evidence_type": e.evidence_type,
             "description": e.description, "url": e.url}
            for e in d.evidence
        ],
        "resolutions": [
            {"id": str(r.id), "resolution_type": r.resolution_type,
             "amount_refunded": float(r.amount_refunded or 0), "amount_released": float(r.amount_released or 0),
             "notes": r.notes, "decided_by": r.decided_by, "decided_at": r.decided_at.isoformat() if r.decided_at else None}
            for r in d.resolutions
        ],
        "timeline": [
            {"id": str(t.id), "event": t.event, "description": t.description,
             "actor": t.actor, "created_at": t.created_at.isoformat() if t.created_at else None}
            for t in d.timeline
        ],
    }


@router.get("/disputes")
async def admin_list_disputes(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """All disputes."""
    query = (select(Dispute).options(selectinload(Dispute.evidence), selectinload(Dispute.resolutions), selectinload(Dispute.timeline)))
    if status:
        query = query.where(Dispute.status == status)
    rows = (await db.execute(query.order_by(Dispute.filed_at.desc()).limit(limit))).scalars().all()
    disputes = [_dispute_dict(d) for d in rows]
    return {
        "disputes": disputes,
        "active_count": sum(1 for d in disputes if d["status"] == "open"),
        "pending_count": sum(1 for d in disputes if d["status"] == "in_review"),
        "resolved_count": sum(1 for d in disputes if d["status"] == "resolved"),
        "total": len(disputes),
    }


@router.get("/disputes/{dispute_id}")
async def admin_dispute_detail(
    dispute_id: UUID,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """A single dispute with evidence, resolutions, timeline."""
    d = (await db.execute(select(Dispute)
                          .options(selectinload(Dispute.evidence), selectinload(Dispute.resolutions), selectinload(Dispute.timeline))
                          .where(Dispute.id == dispute_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Dispute not found")
    payload = _dispute_dict(d)
    # Escrow state drives the "resolve" controls: funds held vs already paid.
    if d.order_id:
        order = (await db.execute(select(Order).where(Order.id == d.order_id))).scalar_one_or_none()
        if order:
            payload.update({
                "order_id": str(order.id),
                "payment_status": getattr(order.payment_status, "value", order.payment_status),
                "inspection_window_hours": order.inspection_window_hours or settings.INSPECTION_WINDOW_HOURS,
                "auto_release_at": order.auto_release_at.isoformat() if order.auto_release_at else None,
                "escrow_released_at": order.escrow_released_at.isoformat() if order.escrow_released_at else None,
            })
    return payload


@router.post("/disputes/{dispute_id}/claim")
async def admin_dispute_claim(
    dispute_id: UUID,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Move an open dispute into review (a human has picked it up)."""
    d = (await db.execute(select(Dispute).where(Dispute.id == dispute_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Dispute not found")
    if d.status == "resolved":
        raise HTTPException(400, "Dispute is already resolved")
    d.status = "in_review"
    db.add(DisputeTimeline(
        dispute_id=d.id,
        event="claimed",
        description="Dispute taken into review by support.",
        actor=str(current_user.get("email") or current_user.get("id", "admin")),
    ))
    await db.commit()
    return {"dispute_id": str(d.id), "status": d.status}


@router.post("/disputes/{dispute_id}/resolve")
async def admin_dispute_resolve(
    dispute_id: UUID,
    payload: dict,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a dispute: record the refund and settle the held escrow.

    The decision is executed here, not by a background task, so the admin sees
    whether the refund was recorded and how much was released:

      * `full_refund_buyer`   - record a refund, release nothing
      * `no_refund_supplier`  - record nothing, release the held escrow
      * `partial_split`       - record `refund_amount`, release the remainder
        (pro-rata across the vendor lines)

    Release is forced past the dispute guard (we are closing that dispute) but
    still respects the already-released idempotency stamp.
    """
    d = (await db.execute(select(Dispute).where(Dispute.id == dispute_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Dispute not found")
    if d.status == "resolved":
        raise HTTPException(400, "Dispute is already resolved")

    order = (await db.execute(
        select(Order).where(Order.id == d.order_id).options(selectinload(Order.items))
    )).scalar_one_or_none() if d.order_id else None

    body = payload or {}
    resolution_type = body.get("resolution_type", "full_refund_buyer")
    notes = body.get("notes", "")
    total = float(order.total_amount or 0) if order else 0.0
    disputed = float(d.amount or 0) or total

    if resolution_type in ("full_refund_buyer", "refund"):
        refund_amount = disputed if total <= 0 else min(disputed or total, total)
        release_scale = 0.0
    elif resolution_type in ("no_refund_supplier", "release_to_supplier"):
        refund_amount = 0.0
        release_scale = 1.0
    elif resolution_type in ("partial_split", "partial_refund"):
        try:
            refund_amount = float(body.get("refund_amount", disputed / 2))
        except (TypeError, ValueError):
            refund_amount = disputed / 2
        refund_amount = min(max(refund_amount, 0.0), total or refund_amount)
        release_scale = max(0.0, 1.0 - (refund_amount / total)) if total > 0 else 0.0
    else:
        raise HTTPException(400, f"Unknown resolution_type '{resolution_type}'")

    actor = str(current_user.get("email") or current_user.get("id", "admin"))
    refund = None
    released_amount = 0.0
    release_result = None

    if order and refund_amount > 0:
        refund = await refund_service.record_refund(
            db,
            order=order,
            amount=round(refund_amount, 2),
            dispute_id=d.id,
            reason=f"Dispute {d.dispute_number} - {resolution_type}. {notes}".strip(),
            requested_by=actor,
            gateway=body.get("gateway") or "manual",
        )

    if order and release_scale > 0:
        release_result = await escrow_service.release_order_escrow_now(
            db, order, force=True, scale=release_scale, commit=False,
        )
        released_amount = float(release_result.get("amount") or 0)

    d.status = "resolved"
    d.resolved_at = datetime.utcnow()
    d.resolved_by = actor
    db.add(DisputeResolution(
        dispute_id=d.id,
        resolution_type=resolution_type,
        amount_refunded=round(refund_amount, 2),
        amount_released=released_amount,
        notes=notes,
        decided_by=actor,
    ))
    db.add(DisputeTimeline(
        dispute_id=d.id,
        event="resolved",
        description=(
            f"{resolution_type}: ₦{refund_amount:,.2f} refunded, "
            f"₦{released_amount:,.2f} released to the supplier."
            + (f" {notes}" if notes else "")
        ),
        actor=actor,
    ))
    await db.commit()

    return {
        "dispute_id": str(d.id),
        "status": d.status,
        "resolution_type": resolution_type,
        "refund": {
            "id": str(refund.id),
            "amount": float(refund.amount or 0),
            "status": refund.status,
        } if refund else None,
        "escrow": release_result or {"released": False, "reason": "no_release_required"},
    }


# ── Refund payouts (the admin escrow/settlement surface) ─────────────────────

def _refund_dict(r: Refund) -> dict:
    return {
        "id": str(r.id),
        "order_id": str(r.order_id) if r.order_id else None,
        "dispute_id": str(r.dispute_id) if r.dispute_id else None,
        "amount": float(r.amount or 0),
        "reason": r.reason,
        "gateway": r.gateway,
        "status": r.status,
        "requested_by": r.requested_by,
        "requested_at": r.requested_at.isoformat() if r.requested_at else None,
        "processed_at": r.processed_at.isoformat() if r.processed_at else None,
        "processed_by": r.processed_by,
        "payout_reference": r.payout_reference,
    }


@router.get("/refunds")
async def admin_list_refunds(
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Refunds instructed by dispute resolutions, awaiting the manual payout."""
    query = select(Refund)
    if status:
        query = query.where(Refund.status == status)
    rows = (await db.execute(
        query.order_by(Refund.requested_at.desc()).limit(limit)
    )).scalars().all()
    refunds = [_refund_dict(r) for r in rows]
    return {
        "refunds": refunds,
        "pending_count": sum(1 for r in refunds if r["status"] == "pending"),
        "pending_amount": round(sum(r["amount"] for r in refunds if r["status"] == "pending"), 2),
        "total": len(refunds),
    }


@router.post("/refunds/{refund_id}/mark-paid")
async def admin_mark_refund_paid(
    refund_id: UUID,
    payload: dict,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Mark a recorded refund as paid once the money has left the platform."""
    row = (await db.execute(select(Refund).where(Refund.id == refund_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Refund not found")
    if row.status == "paid":
        return _refund_dict(row)

    row.status = "paid"
    row.processed_at = datetime.utcnow()
    row.processed_by = str(current_user.get("email") or current_user.get("id", "admin"))
    row.payout_reference = (payload or {}).get("reference") or row.payout_reference
    if (payload or {}).get("gateway"):
        row.gateway = payload["gateway"]
    await db.commit()
    return _refund_dict(row)


# ── Inspection-window override (technical inspection / claim review) ─────────

@router.post("/orders/{order_id}/inspection-extension")
async def admin_extend_inspection_window(
    order_id: UUID,
    payload: dict,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Extend an order's escrow hold (technical inspection, cl. 3C/7-9).

    `hours` defaults to the policy extension (24h); pass 168 for the 7-business-
    day technical-inspection hold. The auto-release task re-checks it.
    """
    order = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    if order.escrow_released_at:
        raise HTTPException(400, "Funds for this order have already been released")

    body = payload or {}
    try:
        hours = int(body.get("hours") or settings.INSPECTION_EXTENSION_HOURS)
    except (TypeError, ValueError):
        hours = settings.INSPECTION_EXTENSION_HOURS
    if hours <= 0 or hours > 24 * 60:
        raise HTTPException(400, "hours must be between 1 and 1440")

    base = order.auto_release_at or order.delivered_at or datetime.utcnow()
    order.auto_release_at = base + timedelta(hours=hours)
    order.inspection_extension_requested_at = order.inspection_extension_requested_at or datetime.utcnow()
    await db.commit()
    return {
        "order_id": str(order.id),
        "auto_release_at": order.auto_release_at.isoformat(),
        "extended_hours": hours,
    }