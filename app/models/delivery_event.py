"""Manual delivery-update feed (driver/vendor live updates).

A `DeliveryJobEvent` is a human-authored update on a dispatch: a driver (or
vendor) manually advancing a job's status, dropping a note ("stuck in
traffic"), or pinning their current location outside the automatic heartbeat.

It is deliberately separate from `driver_location_updates` (the high-frequency
GPS breadcrumb feed): this table stores the low-frequency, semantic updates the
buyer should see *alongside* the live map, with an actor and event type.

- `actor_type`: driver | vendor | admin (who authored the update)
- `event_type`: location | status | note (what kind of update)
- `status`: the job status the actor moved to (when event_type == status)
- `lat/lng`: optional manual location pin (when event_type == location)
- `note`: optional free-text message

Additive only: its own table, no change to `delivery_jobs` / `orders`.
"""
from sqlalchemy import Column, String, Text, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class DeliveryJobEvent(Base):
    __tablename__ = "delivery_job_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    delivery_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("delivery_jobs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Denormalised order link so the buyer feed can be fetched without a join.
    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    actor_type = Column(String(20), default="driver", nullable=False)  # driver | vendor | admin
    actor_name = Column(String(255))

    event_type = Column(String(20), default="status", nullable=False)  # location | status | note | proof
    status = Column(String(20))  # job status when event_type == status

    lat = Column(Numeric(11, 8))
    lng = Column(Numeric(11, 8))
    note = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    job = relationship("DeliveryJob")

    def __repr__(self) -> str:
        return f"<DeliveryJobEvent id={self.id} job={self.delivery_job_id} type={self.event_type}>"
