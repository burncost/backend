"""
TokenPurchase model — a quoted token purchase awaiting (or having received) payment.

The row is written when the gateway checkout is created and is the single source
of truth for the quantity and the amount: a webhook or verification call can only
report that a payment succeeded, never how many tokens to add. It is also the
idempotency guard — a reference that is already `completed` is never credited twice.
"""
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from app.core.database import Base

# Lifecycle states. Stored as plain strings so no new PostgreSQL ENUM type has to
# be created out of band for existing databases (see cads/create_db.py).
PURCHASE_PENDING = "pending"
PURCHASE_COMPLETED = "completed"
PURCHASE_FAILED = "failed"


class TokenPurchase(Base):
    __tablename__ = "token_purchases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Our own reference (TKN-xxxxxxxxxxxx) — also used as the gateway tx_ref /
    # paymentReference, so a webhook can map a charge back to this quote.
    reference = Column(String(100), nullable=False, unique=True, index=True)

    # Server-computed quote (the client never supplies the price).
    tokens = Column(Integer, nullable=False)
    amount_ngn = Column(Integer, nullable=False)
    price_per_token = Column(Integer, nullable=False)

    provider = Column(String(20), nullable=True)             # flutterwave | paystack | monnify | mock
    status = Column(String(20), nullable=False, default=PURCHASE_PENDING, index=True)
    provider_reference = Column(String(100), nullable=True)  # gateway transaction id
    paid_amount_ngn = Column(Integer, nullable=True)         # amount the gateway reported

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
