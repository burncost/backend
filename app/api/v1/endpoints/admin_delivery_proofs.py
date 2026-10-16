"""Admin delivery-proof review queue (Proof of Delivery escalation).

Admin-tier users (via require_roles) can list proofs flagged for review and
record an outcome (clear | reject). This is the escalation/audit surface for
vendor-owned deliveries; the same shared helper (`_apply_pod_review`) backs the
vendor-side review endpoint so buyer-visible events and driver notifications
never drift.

These live under /api/v1/admin/delivery/proofs/* and are mounted with the other
admin routers (prefix "/admin").
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_roles
from app.core.database import get_db
from app.models.delivery import DeliveryJob
from app.models.delivery_proof import DeliveryProof
from app.models.driver import DriverProfile
from app.models.order import Order
from app.models.user import User
# Reuse the single review choke point + serializers from the delivery engine.
from app.api.v1.endpoints.deliveries import _apply_pod_review, _proof_dict
# Reuse the receipt/finalisation choke point so an admin escalation settles the
# order exactly like the buyer's own confirmation does.
from app.api.v1.endpoints.orders import _finalize_delivery_on_receipt, release_order_escrow

logger = logging.getLogger(__name__)
router = APIRouter()

admin_guard = require_roles("manager", "support", "marketing")


class ProofReviewAction(BaseModel):
    action: str = Field(..., pattern="^(clear|reject)$")
    note: Optional[str] = Field(None, max_length=500)


class ProofOnBehalfAction(BaseModel):
    note: Optional[str] = Field(None, max_length=500)


def _queue_item(proof: DeliveryProof, job: DeliveryJob, order: Order, driver: Optional[DriverProfile]) -> dict:
    item = _proof_dict(proof)
    item.update({
        "job_number": job.job_number,
        "order_number": order.order_number,
        "driver_name": driver.full_name if driver else job.driver_name,
        "driver_phone": driver.phone if driver else job.driver_phone,
        "dropoff_city": job.dropoff_city,
        "dropoff_state": job.dropoff_state,
    })
    return item


@router.get("/delivery/proofs")
async def list_delivery_proofs(
    needs_review: Optional[bool] = Query(None, description="Filter to the flagged review queue"),
    review_status: Optional[str] = Query(None, pattern="^(pending|cleared|rejected)$"),
    awaiting_buyer: Optional[bool] = Query(
        None,
        description=(
            "Filter to no-location proofs still awaiting the recipient's receipt "
            "confirmation - the queue that may need an 'accept on behalf' escalation."
        ),
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """List captured proofs, optionally filtered to the flagged review queue."""
    base = (
        select(DeliveryProof, DeliveryJob, Order, DriverProfile)
        .join(DeliveryJob, DeliveryJob.id == DeliveryProof.delivery_job_id)
        .join(Order, Order.id == DeliveryJob.order_id)
        .outerjoin(DriverProfile, DriverProfile.id == DeliveryJob.driver_profile_id)
    )
    if needs_review is not None:
        base = base.where(DeliveryProof.needs_review == needs_review)
    if review_status:
        base = base.where(DeliveryProof.review_status == review_status)
    if awaiting_buyer:
        base = base.where(
            DeliveryProof.buyer_confirmation_required.is_(True),
            DeliveryProof.buyer_confirmed_at.is_(None),
        )

    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar_one()

    rows = (
        await db.execute(
            base.order_by(DeliveryProof.captured_at.desc().nullslast())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "proofs": [_queue_item(p, j, o, d) for p, j, o, d in rows],
    }


@router.post("/delivery/proofs/{proof_id}/review")
async def review_delivery_proof(
    proof_id: UUID,
    payload: ProofReviewAction,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Record an admin review outcome (clear | reject) on a proof."""
    row = await db.execute(
        select(DeliveryProof, DeliveryJob)
        .join(DeliveryJob, DeliveryJob.id == DeliveryProof.delivery_job_id)
        .where(DeliveryProof.id == proof_id)
    )
    first = row.first()
    if not first:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Proof not found")
    proof, job = first

    driver_user_id = None
    if job.driver_profile_id:
        drow = await db.execute(select(DriverProfile.user_id).where(DriverProfile.id == job.driver_profile_id))
        driver_user_id = drow.scalar_one_or_none()

    reviewer_name = getattr(current_user, "email", None) or "Admin"
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


@router.post("/delivery/proofs/{proof_id}/accept-on-behalf")
async def accept_proof_on_behalf(
    proof_id: UUID,
    payload: ProofOnBehalfAction,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Complete a no-location delivery on the recipient's behalf (escalation).

    A photo without location data scores `awaiting_buyer` and blocks the driver's
    own "mark delivered" until the recipient confirms receipt - and the recipient
    may only confirm on the day the photo was taken. When that window is missed
    the delivery would otherwise strand in transit forever, so an admin vouches
    for the receipt here: the proof is flagged cleared, the order and job are
    closed (accruing the driver's earnings) and the vendor escrow is released
    exactly as the buyer's own confirmation would have done.
    """
    row = await db.execute(
        select(DeliveryProof, DeliveryJob, Order)
        .join(DeliveryJob, DeliveryJob.id == DeliveryProof.delivery_job_id)
        .join(Order, Order.id == DeliveryJob.order_id)
        .options(selectinload(Order.items))
        .where(DeliveryProof.id == proof_id)
    )
    first = row.first()
    if not first:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Proof not found")
    proof, job, order = first

    if not proof.buyer_confirmation_required:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail="This proof does not require a receipt confirmation.",
        )
    if proof.buyer_confirmed_at:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail="The recipient has already confirmed this delivery.",
        )
    if order.status in ("cancelled", "refunded"):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot complete a delivery for an order in '{order.status}' status.",
        )

    driver_user_id = None
    if job.driver_profile_id:
        drow = await db.execute(select(DriverProfile.user_id).where(DriverProfile.id == job.driver_profile_id))
        driver_user_id = drow.scalar_one_or_none()

    reviewer_name = getattr(current_user, "email", None) or "Admin"
    _apply_pod_review(
        db,
        proof=proof,
        job=job,
        order_id=job.order_id,
        action="accept_on_behalf",
        note=payload.note,
        reviewer_id=current_user.id,
        reviewer_name=reviewer_name,
        driver_user_id=driver_user_id,
    )
    await _finalize_delivery_on_receipt(
        db,
        order,
        proof,
        confirmed_by=current_user.id,
        actor_type="admin",
        actor_name=reviewer_name,
        note="Receipt accepted on the buyer's behalf by support (photo had no location data).",
    )
    await db.commit()
    await db.refresh(proof)

    # Same background settlement the buyer's own confirmation runs.
    await release_order_escrow(background_tasks, order, db)

    logger.info("Proof %s accepted on the buyer's behalf by admin %s", proof_id, current_user.id)
    return {
        "message": "Delivery completed on the buyer's behalf",
        "proof": _proof_dict(proof),
    }
