from sqlalchemy import Column, Integer, DateTime, Numeric, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class CartItem(Base):
    __tablename__ = "cart_items"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id = Column(UUID(as_uuid=True))
    quantity = Column(Integer, nullable=False, default=1)
    price_at_addition = Column(Numeric(15, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="cart_items")
    
    __table_args__ = (
        UniqueConstraint('user_id', 'product_id', 'variant_id', name='unique_cart_item'),
    )