"""Driver identity, availability and location models.

Phase 1 of the driver & delivery (Uber-for-construction-goods) feature.

Design notes (location-forward):
- `default_lat/default_lng` capture the driver's operating base / default pickup area.
- `current_lat/current_lng/current_location_updated_at` hold the latest live snapshot
  so a future real-time layer can stream position from the same rows.
- `driver_location_updates` is the time-series feed used for live maps, breadcrumbs,
  ETA and "driver is actually moving" checks later.
- `source` = how the driver joined: `self` (self-onboarded / marketplace) or
  `vendor` (onboarded by a vendor; those drivers auto-accept direct assignments).
- `status` = pending | active | suspended   (`availability` is the online/offline toggle).
"""
from sqlalchemy import Column, String, DateTime, Numeric, Boolean, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class DriverProfile(Base):
    __tablename__ = "driver_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Non-null when the driver belongs to a vendor's fleet.
    vendor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    source = Column(String(20), default="self", nullable=False)   # self | vendor
    status = Column(String(20), default="active", nullable=False) # pending | active | suspended

    # Aggregate rating from delivery_ratings (recomputed on submit).
    rating = Column(Numeric(3, 2))          # average rating, 0–5
    rating_count = Column(Integer, default=0)

    full_name = Column(String(255))
    phone = Column(String(20))

    # Online/offline switch the driver toggles to receive/accept delivery offers.
    availability = Column(Boolean, default=False, nullable=False)

    # Vehicle & licensing.
    vehicle_type = Column(String(100))
    vehicle_plate = Column(String(50))
    vehicle_capacity = Column(String(100))
    license_no = Column(String(100))

    # Default / operating location (city + state for MVP matching; coords kept for later).
    default_address = Column(String(500))
    default_city = Column(String(100))
    default_state = Column(String(100))
    default_lat = Column(Numeric(11, 8))
    default_lng = Column(Numeric(11, 8))

    # Latest live location snapshot (updated by the heartbeat endpoint).
    current_lat = Column(Numeric(11, 8))
    current_lng = Column(Numeric(11, 8))
    current_location_updated_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")
    vendor = relationship("Vendor")
    location_updates = relationship("DriverLocationUpdate", back_populates="driver")
    delivery_jobs = relationship("DeliveryJob", back_populates="driver")

    def __repr__(self) -> str:
        return f"<DriverProfile id={self.id} status={self.status}>"


class DriverLocationUpdate(Base):
    """Time-series feed of a driver's location while online / on a delivery job."""

    __tablename__ = "driver_location_updates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    driver_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("driver_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Which job the driver was on (null when idle/online but unassigned).
    delivery_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("delivery_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    lat = Column(Numeric(11, 8), nullable=False)
    lng = Column(Numeric(11, 8), nullable=False)
    accuracy = Column(Numeric(11, 2))  # metres
    speed = Column(Numeric(11, 2))     # m/s
    heading = Column(Numeric(11, 2))   # degrees

    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    driver = relationship("DriverProfile", back_populates="location_updates")
    delivery_job = relationship("DeliveryJob", back_populates="location_updates")

    def __repr__(self) -> str:
        return f"<DriverLocationUpdate id={self.id} driver={self.driver_profile_id}>"