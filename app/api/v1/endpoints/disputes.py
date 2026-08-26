"""User-facing dispute endpoints (Phase 3)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.order import Order
from app.models.dispute import Dispute, DisputeEvidence

router = APIRouter()


@router.post("/")
async def create_dispute(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A party files a dispute against an order."""
    order_id = payload.get("order_id")
    dispute_type = payload.get("dispute_type")
    if not order_id or not dispute_type:
        raise HTTPException(400, "order_id and dispute_type required")
    order = (await db.execute(select(Order).where(Order.id == UUID(order_id)))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    # The filer must be the buyer on the order.
    if order.user_id != current_user.id:
        raise HTTPException(403, "Only the order buyer can file a dispute")

    count = (await db.execute(select(func.count(Dispute.id)))).scalar() or 0
    dispute = Dispute(
        dispute_number=f"DSP-{datetime.utcnow().strftime('%Y%m%d')}-{count + 1:05d}",
        order_id=order.id,
        dispute_type=dispute_type,
        status="open",
        priority=payload.get("priority", "medium"),
        description=payload.get("description", ""),
        buyer_id=current_user.id,
        order_number=order.order_number,
        amount=payload.get("amount", float(order.total_amount or 0)),
        filed_by=str(current_user.id),
    )
    # Optional evidence (submitted by the buyer)
    evidence = payload.get("evidence")
    if evidence and isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict) and item.get("description"):
                db.add(DisputeEvidence(
                    dispute_id=dispute.id,
                    submitted_by="buyer",
                    evidence_type=item.get("evidence_type", "message"),
                    description=item.get("description"),
                    url=item.get("url"),
                ))
    db.add(dispute)
    await db.commit()
    await db.refresh(dispute)
    return {
        "id": str(dispute.id),
        "dispute_number": dispute.dispute_number,
        "status": dispute.status,
    }


@router.get("/my")
async def list_my_disputes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's disputes."""
    rows = (await db.execute(
        select(Dispute).where(Dispute.buyer_id == current_user.id).order_by(Dispute.filed_at.desc())
    )).scalars().all()
    return {"disputes": [
        {
            "id": str(d.id),
            "dispute_number": d.dispute_number,
            "dispute_type": d.dispute_type,
            "status": d.status,
            "priority": d.priority,
            "order_number": d.order_number,
            "amount": float(d.amount or 0),
            "description": d.description,
            "filed_at": d.filed_at.isoformat() if d.filed_at else None,
        }
        for d in rows
    ]}


@router.post("/{dispute_id}/evidence")
async def add_dispute_evidence(
    dispute_id: UUID,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add evidence to an existing dispute (filer only)."""
    d = (await db.execute(select(Dispute).where(Dispute.id == dispute_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Dispute not found")
    if d.buyer_id != current_user.id:
        raise HTTPException(403, "Only the dispute filer can add evidence")
    if d.status == "resolved":
        raise HTTPException(400, "Dispute is already resolved")
    description = payload.get("description")
    if not description:
        raise HTTPException(400, "description required")
    db.add(DisputeEvidence(
        dispute_id=d.id,
        submitted_by="buyer",
        evidence_type=payload.get("evidence_type", "message"),
        description=description,
        url=payload.get("url"),
    ))
    await db.commit()
    return {"dispute_id": str(d.id), "evidence_added": True}