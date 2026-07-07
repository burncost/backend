from sqlalchemy import Column, String, DateTime, Numeric, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class VendorAddress(Base):
    __tablename__ = "vendor_addresses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    address_type = Column(String(20), nullable=False)  # 'warehouse', 'office', 'showroom'
    address_line1 = Column(Text, nullable=False)
    address_line2 = Column(Text)
    city = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False)
    lga = Column(String(100))
    postal_code = Column(String(20))
    landmark = Column(Text)
    latitude = Column(Numeric(10, 8))
    longitude = Column(Numeric(11, 8))
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    vendor = relationship("Vendor", backref="addresses")

    def __repr__(self):
        return f"<VendorAddress {self.address_type} - {self.city}>"
