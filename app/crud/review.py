from app.models.product import Review
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from uuid import UUID


async def get_reviews_by_product(
    db: AsyncSession,
    *,
    product_id: UUID,
    limit: int = 3,
):
    result = await db.execute(
        select(Review)
        .where(Review.product_id == product_id)
        .order_by(Review.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()

async def get_review_stats(db: AsyncSession, product_id: UUID):
    result = await db.execute(
        select(
            func.count(Review.id),
            func.avg(Review.rating)
        ).where(Review.product_id == product_id)
    )

    total, avg = result.first()
    return total or 0, float(avg or 0)