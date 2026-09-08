"""Proof of delivery capture (Phase 1A of the driver & delivery feature).

A `DeliveryProof` is the evidential record captured when a delivery is completed
so an order is only treated as "delivered" with supporting proof. It is additive:
it lives entirely in its own table and does not alter `delivery_jobs`, `orders`
or any onboarding/order lifecycle columns already in use.

- `pod_type`: photo | signature | note  (what was captured)
- `file_url`: stored image URL (Cloudinary / local via StorageService)
- `note`: optional textual evidence (e.g. received by <name>, contact number)
- `captured_lat/lng`: optional geotag at the moment of capture
- `submitted_by`: driver | vendor | admin | buyer (who captured it)

Design constraint: at most ONE proof row per delivery job (unique `delivery_job_id`),
so re-capture updates the existing row instead of creating duplicates.
"""
from sqlalchemy import Column, String, Text, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class DeliveryProof(Base):
    __tablename__ = "delivery_proofs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delivery_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("delivery_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    pod_type = Column(String(20), default="photo", nullable=False)  # photo | signature | note
    file_url = Column(Text)  # stored image URL (photo / signature scan)
    note = Column(Text)

    # Optional geotag captured at the moment of proof.
    captured_lat = Column(Numeric(11, 8))
    captured_lng = Column(Numeric(11, 8))

    # Who captured the proof: driver | vendor | admin | buyer
    submitted_by = Column(String(20), default="driver", nullable=False)

    captured_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    job = relationship("DeliveryJob")

    def __repr__(self) -> str:
        return f"<DeliveryProof id={self.id} job={self.delivery_job_id} type={self.pod_type}>"
