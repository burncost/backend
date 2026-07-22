from sqlalchemy import Column, String, Numeric, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

from app.core.database import Base


class ShippingZone(Base):
    __tablename__ = "shipping_zones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    code = Column(String(20), unique=True, nullable=False)
    base_rate = Column(Numeric(15, 2), nullable=False)
    rate_per_kg = Column(Numeric(10, 2), default=0)
    free_weight_kg = Column(Numeric(10, 2), default=10)
    handling_fee = Column(Numeric(15, 2), default=0)
    estimated_days_min = Column(Integer, default=1)
    estimated_days_max = Column(Integer, default=3)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ShippingZoneMapping(Base):
    __tablename__ = "shipping_zone_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    origin_state = Column(String(100), nullable=False)
    origin_city = Column(String(100), nullable=True)
    destination_state = Column(String(100), nullable=False)
    destination_city = Column(String(100), nullable=True)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("shipping_zones.id"), nullable=False)