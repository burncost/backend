"""User-facing vendor review endpoints (Phase 5)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_review import VendorReview

router = APIRouter()


@router.post("/{vendor_id}/reviews")
async def create_vendor_review(
    vendor_id: UUID,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A customer submits a review for a vendor."""
    rating = payload.get("rating")
    if not rating or not (1 <= int(rating) <= 5):
        raise HTTPException(400, "rating must be between 1 and 5")
    vendor = (await db.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one_or_none()
    if not vendor:
        raise HTTPException(404, "Vendor not found")
    review = VendorReview(
        vendor_id=vendor.id,
        user_id=current_user.id,
        reviewer_name=payload.get("reviewer_name") or f"{current_user.email}",
        rating=int(rating),
        comment=payload.get("comment", ""),
        is_verified_purchase=False,
        is_approved=False,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    # Recompute vendor rating (simple average).
    from sqlalchemy import func
    avg = (await db.execute(select(func.avg(VendorReview.rating)).where(VendorReview.vendor_id == vendor.id))).scalar()
    count = (await db.execute(select(func.count(VendorReview.id)).where(VendorReview.vendor_id == vendor.id))).scalar()
    if avg is not None:
        vendor.rating = round(float(avg), 2)
        vendor.total_reviews = int(count or 0)
        await db.commit()

    return {"id": str(review.id), "status": "pending_approval", "rating": int(rating)}


@router.get("/{vendor_id}/reviews")
async def list_vendor_reviews(
    vendor_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """List approved reviews for a vendor (public)."""
    rows = (await db.execute(
        select(VendorReview)
        .where(VendorReview.vendor_id == vendor_id, VendorReview.is_approved.is_(True))
        .order_by(VendorReview.created_at.desc())
    )).scalars().all()
    return {"reviews": [
        {
            "id": str(r.id),
            "reviewer_name": r.reviewer_name,
            "rating": r.rating,
            "comment": r.comment,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]}