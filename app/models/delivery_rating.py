"""Driver delivery rating (Phase 1B of the driver & delivery feature).

A buyer rates the delivery driver after their order is delivered. Mirrors the
existing `vendor_reviews` pattern but is anchored to a `DeliveryJob` so the
rating is always tied to a real, delivered trip and to the driver who ran it.

- At most ONE rating per delivery job (`delivery_job_id` unique) so a buyer can
  rate each delivery once (edits update the same row).
- `is_verified_purchase` is always True here because only the buyer who owns the
  order on the job can rate it.
- Aggregate `rating` / `rating_count` live on `driver_profiles` and are recomputed
  on each submit, matching how vendor ratings are maintained.
"""
from sqlalchemy import Column, String, Integer, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class DeliveryRating(Base):
    __tablename__ = "delivery_ratings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delivery_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("delivery_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    driver_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("driver_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewer_name = Column(String(100), nullable=True)
    rating = Column(Integer, nullable=False)  # 1..5
    comment = Column(Text)
    is_verified_purchase = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    job = relationship("DeliveryJob")
    driver = relationship("DriverProfile")
    user = relationship("User")

    def __repr__(self) -> str:
        return f"<DeliveryRating id={self.id} job={self.delivery_job_id} rating={self.rating}>"
