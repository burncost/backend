from sqlalchemy import Column, String, Enum as SQLEnum, Boolean, ForeignKey, Text, Numeric, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.core.database import Base


class AddressType(str, enum.Enum):
    HOME = "home"
    OFFICE = "office"
    SITE = "site"
    OTHER = "other"


class CustomerAddress(Base):
    __tablename__ = "customer_addresses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    address_type = Column(
        SQLEnum(AddressType, native_enum=False, length=10),
        default=AddressType.HOME
    )
    contact_name = Column(String(255))
    contact_phone = Column(String(20))
    address_line1 = Column(Text)
    address_line2 = Column(Text)
    city = Column(String(100))
    state = Column(String(100))
    lga = Column(String(100))
    postal_code = Column(String(20))
    landmark = Column(Text)
    latitude = Column(Numeric(10, 8))
    longitude = Column(Numeric(11, 8))
    is_default = Column(Boolean, default=False)
    delivery_instructions = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="addresses")
