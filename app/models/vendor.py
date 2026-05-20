from sqlalchemy import Column, String, Enum as SQLEnum, DateTime, Numeric, Integer, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
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


class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        unique=True, 
        nullable=False)
    business_name = Column(String(255), nullable=True)
    business_type = Column(String(100), nullable=True)
    business_address = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(50), nullable=True)

    cac_business_registration_number = Column(String(20), default=0, nullable=True)
    tax_identification_number = Column(String(20), default=0, nullable=True)
    verification_status = Column(
        SQLEnum(VendorVerificationStatus, native_enum=False, length=20),
        default=VendorVerificationStatus.PENDING
    )

    # documents
    cac_certificate = Column(String(255), nullable=True)
    tax_clearance = Column(String(255), nullable=True)
    business_license = Column(String(255), nullable=True)
    utility_bill = Column(String(255), nullable=True)

    bank_account_name = Column(String(255), nullable=False)
    bank_account_number = Column(String(50), default=0, nullable=False)
    bank_name = Column(String(100), nullable=False)
    verification_date = Column(DateTime, nullable=True)
    verified_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    commission_rate = Column(Numeric(5, 2), default=10.00)
    rating = Column(Numeric(3, 2), default=0.00)
    total_reviews = Column(Integer, default=0)
    total_sales = Column(Numeric(15, 2), default=0.00)
    is_featured = Column(Boolean, default=False)
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
        backref="vendors_verified"
    )
    products = relationship("Product", back_populates="vendor", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Vendor {self.business_name}>"
    