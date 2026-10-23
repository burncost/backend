"""User-facing dispute endpoints (Phase 3).

Implements the buyer/supplier half of the published dispute-resolution policy:
either party on an order may file while the funds are still held, evidence is
attached in the same transaction as the dispute (the old code created evidence
before the dispute was flushed, so a filing with evidence 500'd), and every
step appends to `dispute_timeline` for the admin review screen.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_user, get_current_vendor
from app.models.user import User
from app.models.order import Order, OrderItem
from app.models.vendor import Vendor
from app.models.dispute import Dispute, DisputeEvidence, DisputeTimeline

router = APIRouter()

# Statuses that keep the escrow frozen (mirrors escrow_service).
ACTIVE_DISPUTE_STATUSES = ("open", "in_review", "escalated")


def _enum(value):
    return getattr(value, "value", value)


async def _filer_vendor_id(db: AsyncSession, order: Order, current_user: User):
    """Vendor id when `current_user` supplies a line on this order, else None."""
    if current_user.role not in ("vendor", "admin", "super_admin"):
        return None
    row = await db.execute(
        select(OrderItem.vendor_id)
        .where(
            OrderItem.order_id == order.id,
            OrderItem.vendor_id.in_(select(Vendor.id).where(Vendor.user_id == current_user.id)),
        )
        .limit(1)
    )
    return row.scalar_one_or_none()


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
    try:
        uid = UUID(str(order_id))
    except ValueError:
        raise HTTPException(400, "Invalid order ID")

    result = await db.execute(select(Order).where(Order.id == uid))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")

    # Either the buyer, or a supplier with a line on the order.
    is_buyer = str(order.user_id) == str(current_user.id)
    vendor_id = None
    if not is_buyer:
        vendor_id = await _filer_vendor_id(db, order, current_user)
        if vendor_id is None:
            raise HTTPException(403, "Only the order buyer or a supplier on the order can file a dispute")

    # Funds must still be held: once released (or refunded) there is nothing to
    # freeze, so the claim has to go through support instead.
    status_value = _enum(order.status)
    payment_value = _enum(order.payment_status)
    if status_value in ("cancelled", "refunded") or payment_value == "refunded":
        raise HTTPException(400, "This order can no longer be disputed")
    if status_value in ("pending_payment", "payment_failed") or payment_value not in ("completed", "partially_refunded"):
        raise HTTPException(400, "This order has not been paid for")
    if order.escrow_released_at:
        raise HTTPException(400, "The inspection window has closed and the funds have been released")

    existing = (await db.execute(
        select(Dispute.id).where(
            Dispute.order_id == order.id,
            Dispute.status.in_(ACTIVE_DISPUTE_STATUSES),
        ).limit(1)
    )).first()
    if existing:
        raise HTTPException(400, "An open dispute already exists for this order")

    total = float(order.total_amount or 0)
    try:
        requested = float(payload.get("amount") or total)
    except (TypeError, ValueError):
        requested = total
    amount = round(min(max(requested, 0.0), total), 2) if total > 0 else 0.0

    if vendor_id is None:
        vendor_id = (await db.execute(
            select(OrderItem.vendor_id).where(OrderItem.order_id == order.id).limit(1)
        )).scalar_one_or_none()

    count = (await db.execute(select(func.count(Dispute.id)))).scalar() or 0
    dispute = Dispute(
        dispute_number=f"DSP-{datetime.utcnow().strftime('%Y%m%d')}-{count + 1:05d}",
        order_id=order.id,
        dispute_type=dispute_type,
        status="open",
        priority=payload.get("priority", "medium"),
        description=payload.get("description", ""),
        buyer_id=order.user_id,
        supplier_id=vendor_id,
        order_number=order.order_number,
        amount=amount,
        filed_by=str(current_user.id),
    )
    db.add(dispute)
    # The dispute id is generated application-side, but the row must exist
    # before evidence/timeline rows reference it - flush first (P0 regression:
    # filing with evidence used to raise a foreign-key violation).
    await db.flush()

    evidence = payload.get("evidence")
    if evidence and isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict) and item.get("description"):
                db.add(DisputeEvidence(
                    dispute_id=dispute.id,
                    submitted_by="buyer" if is_buyer else "supplier",
                    evidence_type=item.get("evidence_type", "message"),
                    description=item.get("description"),
                    url=item.get("url"),
                ))

    db.add(DisputeTimeline(
        dispute_id=dispute.id,
        event="filed",
        description=(f"{dispute_type} reported on order {order.order_number} "
                     f"for ₦{amount:,.2f}. Funds held pending review."),
        actor="buyer" if is_buyer else "supplier",
    ))
    await db.commit()
    await db.refresh(dispute)

    # Admin → Settings → Notifications → "Dispute Escalations".
    from app.services import alert_settings
    await alert_settings.notify_admins(
        db,
        kind="dispute",
        type_name="dispute",
        title="New dispute filed",
        message=(f"{dispute.dispute_number}: {dispute_type} reported on order "
                 f"{order.order_number} (₦{float(dispute.amount or 0):,.2f})."),
        link=f"/disputes/{dispute.id}",
    )

    return {
        "id": str(dispute.id),
        "dispute_number": dispute.dispute_number,
        "status": dispute.status,
        "amount": float(dispute.amount or 0),
        "escrow_hold": True,
        "auto_release_at": order.auto_release_at.isoformat() if order.auto_release_at else None,
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
    db.add(DisputeTimeline(
        dispute_id=d.id,
        event="evidence_added",
        description=description[:500],
        actor="buyer",
    ))
    await db.commit()
    return {"dispute_id": str(d.id), "evidence_added": True}


@router.get("/vendor")
async def list_vendor_disputes(
    vendor=Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db),
):
    """Disputes raised against this supplier's orders (supplier dashboard)."""
    rows = (await db.execute(
        select(Dispute)
        .where(Dispute.supplier_id == UUID(str(vendor["id"])))
        .order_by(Dispute.filed_at.desc())
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


@router.get("/{dispute_id}")
async def get_dispute(
    dispute_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dispute detail with its evidence and timeline (parties + admins)."""
    d = (await db.execute(select(Dispute).where(Dispute.id == dispute_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Dispute not found")

    allowed = str(d.buyer_id) == str(current_user.id) or current_user.role in ("admin", "super_admin")
    if not allowed and str(d.supplier_id or "") != "":
        allowed = (await db.execute(
            select(Vendor.id).where(
                Vendor.id == d.supplier_id,
                Vendor.user_id == current_user.id,
            )
        )).first() is not None
    if not allowed:
        raise HTTPException(403, "Not authorized to view this dispute")

    evidence = (await db.execute(
        select(DisputeEvidence)
        .where(DisputeEvidence.dispute_id == d.id)
        .order_by(DisputeEvidence.created_at.asc())
    )).scalars().all()
    timeline = (await db.execute(
        select(DisputeTimeline)
        .where(DisputeTimeline.dispute_id == d.id)
        .order_by(DisputeTimeline.created_at.asc())
    )).scalars().all()

    order = (await db.execute(select(Order).where(Order.id == d.order_id))).scalar_one_or_none()
    return {
        "id": str(d.id),
        "dispute_number": d.dispute_number,
        "order_id": str(d.order_id) if d.order_id else None,
        "order_number": d.order_number,
        "dispute_type": d.dispute_type,
        "status": d.status,
        "priority": d.priority,
        "description": d.description,
        "amount": float(d.amount or 0),
        "filed_at": d.filed_at.isoformat() if d.filed_at else None,
        "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
        # The escrow clock, so both parties can see the hold.
        "auto_release_at": order.auto_release_at.isoformat() if order and order.auto_release_at else None,
        "escrow_released_at": (
            order.escrow_released_at.isoformat() if order and order.escrow_released_at else None
        ),
        "evidence": [
            {
                "id": str(e.id),
                "submitted_by": e.submitted_by,
                "evidence_type": e.evidence_type,
                "description": e.description,
                "url": e.url,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in evidence
        ],
        "timeline": [
            {
                "event": t.event,
                "description": t.description,
                "actor": t.actor,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in timeline
        ],
    }


@router.post("/{dispute_id}/respond")
async def respond_to_dispute(
    dispute_id: UUID,
    payload: dict,
    vendor=Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db),
):
    """Supplier response: evidence + a timeline entry, moving it to review."""
    d = (await db.execute(select(Dispute).where(Dispute.id == dispute_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Dispute not found")
    if str(d.supplier_id or "") != str(vendor["id"]):
        raise HTTPException(403, "Only the supplier on this dispute can respond")
    if d.status == "resolved":
        raise HTTPException(400, "Dispute is already resolved")

    description = (payload.get("description") or "").strip()
    if not description:
        raise HTTPException(400, "description required")

    db.add(DisputeEvidence(
        dispute_id=d.id,
        submitted_by="supplier",
        evidence_type=payload.get("evidence_type", "message"),
        description=description,
        url=payload.get("url"),
    ))
    if d.status == "open":
        d.status = "in_review"
    db.add(DisputeTimeline(
        dispute_id=d.id,
        event="supplier_responded",
        description=description[:500],
        actor="supplier",
    ))
    await db.commit()
    return {"dispute_id": str(d.id), "status": d.status}