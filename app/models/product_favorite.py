"""
Product favourites — a per-user saved-products list.

Guests keep favourites in localStorage (mirrors the guest-cart flow); on
sign-in the client replays them to `POST /favorites/{product_id}`, so this
table only ever stores rows for authenticated users.
"""
from sqlalchemy import Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class ProductFavorite(Base):
    """A single saved product for a user (unique per user+product pair)."""
    __tablename__ = "product_favorites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    product = relationship("Product")

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_product_favorites_user_product"),
    )

    def __repr__(self):
        return f"<ProductFavorite user={self.user_id} product={self.product_id}>"
