"""Phase 5 admin dispute endpoints — Disputes, Details, Resolution.

Powered by the new dispute models.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime
from uuid import UUID
from typing import Optional

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.dispute import Dispute, DisputeEvidence, DisputeResolution, DisputeTimeline

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
        "created_date": d.filed_at.strftime("%b %d, %Y") if d.filed_at else "—",
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
    return _dispute_dict(d)


@router.post("/disputes/{dispute_id}/resolve")
async def admin_dispute_resolve(
    dispute_id: UUID,
    payload: dict,
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a dispute with a resolution type and refund/release split."""
    d = (await db.execute(select(Dispute).where(Dispute.id == dispute_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Dispute not found")
    resolution_type = (payload or {}).get("resolution_type", "full_refund_buyer")
    amount = float(d.amount or 0)
    notes = (payload or {}).get("notes", "")
    if resolution_type == "full_refund_buyer":
        refunded, released = amount, 0
    elif resolution_type == "no_refund_supplier":
        refunded, released = 0, amount
    else:  # partial_split
        refunded = released = amount / 2
    d.status = "resolved"
    d.resolved_at = datetime.utcnow()
    d.resolved_by = str(current_user.get("id", "admin"))
    db.add(DisputeResolution(
        dispute_id=d.id, resolution_type=resolution_type,
        amount_refunded=refunded, amount_released=released, notes=notes,
        decided_by=str(current_user.get("id", "admin")),
    ))
    await db.commit()
    return {"dispute_id": str(d.id), "status": d.status}