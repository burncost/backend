from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional
from uuid import UUID


class ReviewOut(BaseModel):
    id: UUID
    reviewer_name: Optional[str] = None
    rating: int
    comment: str
    created_at: datetime

    class Config:
        from_attributes = True


class ProductReviewsResponse(BaseModel):
    # product_id: UUID
    average_rating: float
    total_reviews: int
    reviews: List[ReviewOut]
