from sqlalchemy import Column, String, Numeric, JSON, Boolean, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid
from app.core.database import Base


class VendorVerificationTier(Base):
    """Vendor verification tier lookup (cac_only / documented / trusted)."""
    __tablename__ = "vendor_verification_tiers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tier_code = Column(String(20), unique=True, nullable=False, index=True)
    display_name = Column(String(50), nullable=False)
    sort_order = Column(Integer, default=1)
    transaction_cap = Column(Numeric(16, 2), nullable=False, default=5_000_000)
    commission_rate = Column(Numeric(5, 2), nullable=False, default=10.00)
    required_document_types = Column(JSON, default=list)
    requires_manual_review = Column(Boolean, default=False)
    perks = Column(JSON, default=list)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<VendorVerificationTier {self.tier_code}>"