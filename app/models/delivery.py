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
from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey, Boolean
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
    # Set once a driver is assigned (auto for `direct`, on bid accept for `bid`).
    driver_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("driver_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    assignment_type = Column(String(20), default="bid", nullable=False)  # direct | bid
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

    # ── Post-confirmation delivery arrangement ──────────────────────────────
    # `bid` jobs collect driver bids until this moment (default: confirmed + 48h).
    # The buyer may also accept the vendor's quote at any point before that.
    bidding_closes_at = Column(DateTime, index=True)
    # False until the buyer pays the delivery invoice covering this job. A job with
    # a zero fee (free vendor delivery) is paid by definition. Legacy rows default
    # to True so pre-existing dispatched jobs keep working untouched.
    shipping_paid = Column(Boolean, default=True, nullable=False, server_default="true", index=True)
    # Invoice that settled this job (audit trail for the consolidated payment).
    shipping_invoice_id = Column(
        UUID(as_uuid=True),
        ForeignKey("shipping_invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Buyer declined the vendor's transport quote; a vendor may re-quote once.
    vendor_fee_declined_at = Column(DateTime)

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
    bids = relationship("DeliveryBid", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<DeliveryJob id={self.id} status={self.status}>"