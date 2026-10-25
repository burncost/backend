"""Public (unauthenticated) endpoints for the marketing/landing experience."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, union_all, literal_column

from app.core.database import get_db
from app.models.blog_post import BlogPost
from app.models.product import Review
from app.models.vendor_review import VendorReview
from app.services.blog_service import post_to_dict

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
    testimonial feed for the landing page. Never raises - returns [] on error
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


# ── Blog (public reads) ─────────────────────────────────────────────────────
# Only published posts are visible; drafts are an admin-only concept.

@router.get("/blog")
async def list_public_blog_posts(
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    tag: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    try:
        query = select(BlogPost).where(BlogPost.status == "published")
        if tag:
            query = query.where(BlogPost.tags.ilike(f"%{tag}%"))

        total = (await db.execute(
            select(func.count()).select_from(query.subquery())
        )).scalar() or 0
        rows = (await db.execute(
            query.order_by(BlogPost.published_at.desc().nullslast(), BlogPost.created_at.desc())
            .offset(offset)
            .limit(limit)
        )).scalars().all()

        return {
            "total": total,
            "posts": [post_to_dict(p, include_content=False) for p in rows],
        }
    except Exception as e:  # pragma: no cover - a public page must not break
        logger.error("Failed to load public blog posts: %s", e)
        return {"total": 0, "posts": []}


@router.get("/blog/{slug}")
async def get_public_blog_post(
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    post = (await db.execute(
        select(BlogPost).where(
            BlogPost.slug == slug,
            BlogPost.status == "published",
        )
    )).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    # View count is best-effort: a failed counter must never block the read.
    try:
        post.view_count = int(post.view_count or 0) + 1
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning("Blog view count update failed for %s: %s", slug, exc)

    return post_to_dict(post)
