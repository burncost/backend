from sqlalchemy import Column, String, Enum as SQLEnum, DateTime, Numeric, Integer, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.core.database import Base


class VendorVerificationStatus(str, enum.Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        unique=True, 
        nullable=False)
    business_name = Column(String(255), nullable=False)
    business_type = Column(String(255), nullable=False)
    city = Column(String(255), nullable=False)
    state = Column(String(255), nullable=False)
    business_address = Column(String(255), nullable=False)
    cac_business_registration_number = Column(String(100), unique=True, nullable=True)
    tax_identification_number = Column(String(50), nullable=True)
    verification_status = Column(
        SQLEnum(VendorVerificationStatus, native_enum=False, length=20,
                values_callable=lambda e: [m.value for m in e]),
        default=VendorVerificationStatus.PENDING
    )
    # Verification tier: cac_only (1), documented (2), trusted (3)
    verification_tier = Column(String(20), default="cac_only", nullable=False)
    # Soft-limit transaction volume (NGN), checked against tier cap
    transaction_volume = Column(Numeric(15, 2), default=0.00)
    verification_date = Column(DateTime, nullable=True)
    verified_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    commission_rate = Column(Numeric(5, 2), default=10.00)
    rating = Column(Numeric(3, 2), default=0.00)
    total_reviews = Column(Integer, default=0)
    total_sales = Column(Numeric(15, 2), default=0.00)
    is_featured = Column(Boolean, default=False)
    business_image = Column(String(500), nullable=True)
    delivery_time = Column(String(100), default="1-3 Days")
    response_time = Column(String(100), default="< 1 hour")
    specializations = Column(ARRAY(String), default=[])
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship(
        "User",
        back_populates="vendor",
        foreign_keys=[user_id]
    )
    verifier = relationship(
        "User",
        foreign_keys=[verified_by],
        back_populates="vendors_verified"
    )
    products = relationship("Product", back_populates="vendor", cascade="all, delete-orphan")
    bank_accounts = relationship("VendorBankAccount", back_populates="vendor", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Vendor {self.business_name}>"
    
