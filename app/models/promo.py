from sqlalchemy import Column, String, DateTime, Numeric, Integer, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

from app.core.database import Base


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(Text)
    discount_percent = Column(Numeric(5, 2), nullable=False)
    max_uses = Column(Integer, default=0)
    current_uses = Column(Integer, default=0)
    min_order_amount = Column(Numeric(15, 2), default=0)
    is_active = Column(Boolean, default=True)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<PromoCode {self.code}>"
