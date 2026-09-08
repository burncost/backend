"""Delivery / dispatch model (Phase 1 of the driver & delivery feature).

A `DeliveryJob` is a unit of work moving an order from a vendor to the buyer.

Assignment types:
- `direct` -> vendor picks one of their fleet drivers; the job is AUTO-ACCEPTED
  at creation (no driver opt-in needed).
- `offer`  -> published to nearby available drivers; the first to accept wins.

Location-forward:
- pickup/dropoff addresses AND coordinates are captured at dispatch time so
  later phases can do distance matching, ETA and live map views without a rewrite.
- job lifecycle timestamps give a clean timeline for tracking/live-progress.
"""
from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class DeliveryJob(Base):
    __tablename__ = "delivery_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_number = Column(String(50), unique=True, nullable=False, index=True)

    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Set once a driver is assigned (auto for `direct`, on accept for `offer`).
    driver_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("driver_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    assignment_type = Column(String(20), default="offer", nullable=False)  # direct | offer
    # available | accepted | picked_up | in_transit | delivered | cancelled | expired
    status = Column(String(20), default="available", nullable=False, index=True)

    # Pickup (vendor location).
    pickup_address = Column(String(500))
    pickup_city = Column(String(100))
    pickup_state = Column(String(100))
    pickup_lat = Column(Numeric(11, 8))
    pickup_lng = Column(Numeric(11, 8))

    # Dropoff (buyer shipping address).
    dropoff_address = Column(String(500))
    dropoff_city = Column(String(100))
    dropoff_state = Column(String(100))
    dropoff_lat = Column(Numeric(11, 8))
    dropoff_lng = Column(Numeric(11, 8))

    # Driver snapshot for buyer/admin display + order sync.
    driver_name = Column(String(255))
    driver_phone = Column(String(20))

    # Delivery fee snapshot (charged/settled to driver in a later phase).
    delivery_fee = Column(Numeric(15, 2), default=0)

    # Lifecycle timestamps (progress timeline / live tracking).
    accepted_at = Column(DateTime)
    picked_up_at = Column(DateTime)
    in_transit_at = Column(DateTime)
    delivered_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    expires_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    order = relationship("Order")
    driver = relationship("DriverProfile", back_populates="delivery_jobs")
    location_updates = relationship("DriverLocationUpdate", back_populates="delivery_job")

    def __repr__(self) -> str:
        return f"<DeliveryJob id={self.id} status={self.status}>"