from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.models.product import Review, Product
from app.models.user import User
from app.api.deps import get_current_user, get_current_vendor
from app.schemas.review import ReviewOut, ProductReviewsResponse
from app.models.vendor import Vendor

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


### Create a review for a product
@router.post("/", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
async def create_review(
    product_id: UUID,
    rating: int = Query(..., ge=1, le=5),
    comment: str = Query(""),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Reject anonymous users
    if current_user.role == "anonymous" or str(current_user.id) == "anonymous":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to submit a review"
        )

    # Verify product exists
    product_result = await db.execute(select(Product).where(Product.id == product_id))
    product = product_result.scalar_one_or_none()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found"
        )

    # Check if user already reviewed this product
    existing_result = await db.execute(
        select(Review).where(
            Review.product_id == product_id,
            Review.user_id == current_user.id
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already reviewed this product"
        )

    # Get user name for reviewer_name
    user_result = await db.execute(
        select(User).where(User.id == current_user.id)
    )
    user = user_result.scalar_one_or_none()
    reviewer_name = user.profile.first_name if user and user.profile else current_user.email

    review = Review(
        product_id=product_id,
        user_id=current_user.id,
        reviewer_name=reviewer_name,
        rating=rating,
        comment=comment,
    )
    db.add(review)

    # Update product rating stats
    stats_result = await db.execute(
        select(func.count(Review.id), func.avg(Review.rating))
        .where(Review.product_id == product_id)
    )
    total, avg = stats_result.first()
    product.review_count = (total or 0) + 1
    product.rating = round(((avg or 0) * (total or 0) + rating) / ((total or 0) + 1), 2)

    await db.commit()
    await db.refresh(review)

    logger.info(f"Review created for product {product_id} by user {current_user.id}")
    return review


### List reviews for a product
@router.get("/product/{product_id}", response_model=ProductReviewsResponse)
async def list_product_reviews(
    product_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    # Get paginated reviews
    result = await db.execute(
        select(Review)
        .where(Review.product_id == product_id)
        .order_by(Review.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    reviews = result.scalars().all()

    # Get stats
    stats_result = await db.execute(
        select(func.count(Review.id), func.avg(Review.rating))
        .where(Review.product_id == product_id)
    )
    total, avg = stats_result.first()

    return {
        "average_rating": round(float(avg or 0), 2),
        "total_reviews": total or 0,
        "reviews": reviews,
    }


### List all reviews for the current vendor's products
@router.get("/vendor/my-products")
async def list_vendor_product_reviews(
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    try:
        vendor_id = UUID(current_vendor["id"])

        # Get all products belonging to this vendor
        products_result = await db.execute(
            select(Product.id, Product.name).where(Product.vendor_id == vendor_id)
        )
        products = products_result.all()
        product_ids = [p.id for p in products]
        product_map = {p.id: p.name for p in products}

        if not product_ids:
            return {
                "reviews": [],
                "total_reviews": 0,
                "average_rating": 0,
                "rating_distribution": {str(i): 0 for i in range(1, 6)},
            }

        # Get all reviews for those products
        reviews_result = await db.execute(
            select(Review)
            .where(Review.product_id.in_(product_ids))
            .order_by(Review.created_at.desc())
        )
        reviews = reviews_result.scalars().all()

        # Calculate stats
        total_reviews = len(reviews)
        average_rating = round(sum(r.rating for r in reviews) / total_reviews, 2) if total_reviews > 0 else 0

        # Rating distribution
        rating_distribution = {str(i): 0 for i in range(1, 6)}
        for r in reviews:
            rating_distribution[str(r.rating)] = rating_distribution.get(str(r.rating), 0) + 1

        # Attach product name to each review
        review_list = []
        for r in reviews:
            review_dict = {
                "id": str(r.id),
                "product_id": str(r.product_id),
                "product_name": product_map.get(r.product_id, "Unknown"),
                "reviewer_name": r.reviewer_name,
                "rating": r.rating,
                "comment": r.comment,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            review_list.append(review_dict)

        return {
            "reviews": review_list,
            "total_reviews": total_reviews,
            "average_rating": average_rating,
            "rating_distribution": rating_distribution,
        }

    except Exception as e:
        logger.error(f"Failed to fetch vendor product reviews: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load reviews. Please try again later."
        )


### List all reviews by the current user
@router.get("/my", response_model=List[ReviewOut])
async def list_my_reviews(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Review)
        .where(Review.user_id == current_user.id)
        .order_by(Review.created_at.desc())
    )
    return result.scalars().all()


### Update a review
@router.put("/{review_id}", response_model=ReviewOut)
async def update_review(
    review_id: UUID,
    rating: Optional[int] = Query(None, ge=1, le=5),
    comment: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found"
        )

    # Only the review author can update
    if str(review.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this review"
        )

    if rating is not None:
        review.rating = rating
    if comment is not None:
        review.comment = comment

    await db.commit()
    await db.refresh(review)

    logger.info(f"Review {review_id} updated by user {current_user.id}")
    return review


### Delete a review
@router.delete("/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(
    review_id: UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found"
        )

    # Only the review author or admin can delete
    if str(review.user_id) != str(current_user.id) and current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this review"
        )

    await db.delete(review)
    await db.commit()
    return None
