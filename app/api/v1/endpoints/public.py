"""Public (unauthenticated) endpoints for the marketing/landing experience."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, union_all, literal_column

from app.core.database import get_db
from app.models.product import Review
from app.models.vendor_review import VendorReview

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _initials(name: str | None) -> str:
    if not name:
        return "BC"
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "BC"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@router.get("/testimonials")
async def list_testimonials(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Aggregate approved reviews across products and vendors into a public
    testimonial feed for the landing page. Never raises — returns [] on error
    so a public page cannot be broken by an empty/unhealthy DB."""
    try:
        if limit < 1:
            limit = 1
        if limit > 100:
            limit = 100

        product_rows = (
            select(
                Review.reviewer_name.label("reviewer_name"),
                Review.rating.label("rating"),
                Review.comment.label("comment"),
                Review.created_at.label("created_at"),
                literal_column("'product'").label("source"),
            )
            .where(
                Review.is_approved.is_(True),
                Review.comment.isnot(None),
                Review.reviewer_name.isnot(None),
            )
        )
        vendor_rows = (
            select(
                VendorReview.reviewer_name.label("reviewer_name"),
                VendorReview.rating.label("rating"),
                VendorReview.comment.label("comment"),
                VendorReview.created_at.label("created_at"),
                literal_column("'vendor'").label("source"),
            )
            .where(
                VendorReview.is_approved.is_(True),
                VendorReview.comment.isnot(None),
                VendorReview.reviewer_name.isnot(None),
            )
        )

        combined = union_all(product_rows, vendor_rows).order_by(
            literal_column("created_at").desc()
        ).limit(limit)

        result = await db.execute(combined)
        rows = result.all()

        return {
            "total": len(rows),
            "testimonials": [
                {
                    "reviewer_name": r.reviewer_name,
                    "initials": _initials(r.reviewer_name),
                    "rating": int(r.rating or 0),
                    "comment": (r.comment or "").strip(),
                    "source": r.source,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
                if r.comment
            ],
        }
    except Exception as e:  # pragma: no cover - defensive for public endpoint
        logger.error(f"Failed to load public testimonials: {str(e)}")
        return {"total": 0, "testimonials": []}
