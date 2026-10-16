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
    # Flat discount amount in Naira (whole number, e.g. 1000 = ₦1,000 off) -
    # not a percentage. Capped at the eligible subtotal at checkout.
    discount_amount = Column(Numeric(15, 2), nullable=False)
    # Global cap on redemptions across all users (0 = unlimited).
    max_uses = Column(Integer, default=0)
    current_uses = Column(Integer, default=0)
    # Per-user cap (0 = unlimited). The number of times a single buyer may redeem
    # this code, counted from their non-cancelled orders carrying the code.
    max_uses_per_user = Column(Integer, default=0)
    min_order_amount = Column(Numeric(15, 2), default=0)
    is_active = Column(Boolean, default=True)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=True)
    # Scope: platform (admin, all products) | vendor (all of the owner's products)
    # | product (one specific product) | category (products in a category).
    # scope_id holds the product_id / category_id for the product/category scopes.
    scope_type = Column(String(20), nullable=False, default="platform", server_default="platform")
    scope_id = Column(UUID(as_uuid=True), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<PromoCode {self.code}>"
