"""DeliveryBid model - a driver's price offer on a `bid` delivery job.

Buyers compare bids per vendor group and accept one. The accepted bid becomes the
job's delivery fee and must be paid (via `ShippingInvoice`) before the driver can
pick the goods up. Bids are per (job, driver): re-bidding replaces the pending
amount instead of stacking rows, so the buyer never sees two prices from the same
driver on one job.
"""
from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base

# Lifecycle states (plain strings - no PostgreSQL ENUM to maintain out of band).
BID_PENDING = "pending"
BID_ACCEPTED = "accepted"
BID_REJECTED = "rejected"
BID_WITHDRAWN = "withdrawn"
BID_EXPIRED = "expired"


class DeliveryBid(Base):
    __tablename__ = "delivery_bids"
    __table_args__ = (
        UniqueConstraint("job_id", "driver_profile_id", name="uq_delivery_bids_job_driver"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("delivery_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("driver_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # What the driver asks for this vendor group's delivery.
    amount = Column(Numeric(15, 2), nullable=False)
    note = Column(String(500))

    # pending | accepted | rejected | withdrawn | expired
    status = Column(String(20), default=BID_PENDING, nullable=False, index=True)
    decided_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    job = relationship("DeliveryJob", back_populates="bids")
    driver = relationship("DriverProfile")

    def __repr__(self) -> str:
        return f"<DeliveryBid id={self.id} job={self.job_id} amount={self.amount} status={self.status}>"
