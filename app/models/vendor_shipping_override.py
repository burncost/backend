from sqlalchemy import Column, Numeric, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

from app.core.database import Base


class VendorShippingOverride(Base):
    __tablename__ = "vendor_shipping_overrides"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("shipping_zones.id"), nullable=False)
    custom_base_rate = Column(Numeric(15, 2))
    custom_rate_per_kg = Column(Numeric(10, 2))
    free_shipping_threshold = Column(Numeric(15, 2))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)