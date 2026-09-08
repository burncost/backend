"""Independent-driver earnings ledger (Phase 1C of the driver & delivery feature).

Records what Burncost owes a driver for a completed delivery. Only *independent,
marketplace* drivers (self-onboarded, `source = self`, `vendor_id = NULL`) earn
marketplace payouts here. Fleet drivers (`vendor_id` set) are paid by their vendor
through the existing escrow -> vendor transfer flow and are intentionally excluded.

- One row per delivered job (`delivery_job_id` unique) so a driver can never be
  credited twice for the same delivery (idempotent).
- `gross_amount` is the delivery fee snapshot on the job; `commission_amount` is
  Burncost's cut; `net_amount` = what the driver should be paid.
- `status`: pending | paid | cancelled (payout automation lands with the
  Settlement & Disbursement phase; this phase only accrues the ledger).
"""
from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class DriverEarnings(Base):
    __tablename__ = "driver_earnings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    driver_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("driver_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delivery_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("delivery_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    gross_amount = Column(Numeric(15, 2), nullable=False)
    commission_amount = Column(Numeric(15, 2), default=0, nullable=False)
    net_amount = Column(Numeric(15, 2), nullable=False)

    # pending | paid | cancelled
    status = Column(String(20), default="pending", nullable=False)
    paid_at = Column(DateTime)
    payout_reference = Column(String(100))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    driver = relationship("DriverProfile")
    job = relationship("DeliveryJob")

    def __repr__(self) -> str:
        return f"<DriverEarnings id={self.id} driver={self.driver_profile_id} net={self.net_amount}>"
