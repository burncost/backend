"""Payment gateway capture record (Phase 5 — Settlement & Disbursement).

Persists, per order, *which* gateway actually captured the funds plus the provider
transaction reference, amount and gateway fee. Today the code decides the gateway
at init/verify time but does not record it durably — this closes that gap so any
later disbursement pays out on the platform that captured the money.

INERT: nothing writes to this automatically yet. Recording is done through the
flag-gated settlement service / admin endpoints; a staging hook will call it from
the payment verify path behind `SETTLEMENT_ENABLED`.
"""
from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class PaymentCapture(Base):
    __tablename__ = "payment_captures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    gateway = Column(String(20), nullable=False, index=True)  # flutterwave | paystack | monnify | mock
    provider_reference = Column(String(255))
    amount = Column(Numeric(15, 2), nullable=False)
    gateway_fee = Column(Numeric(15, 2), default=0, nullable=False)
    currency = Column(String(10), default="NGN", nullable=False)

    # captured | failed (captured is what matters for settlement)
    status = Column(String(20), default="captured", nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    order = relationship("Order")

    def __repr__(self) -> str:
        return f"<PaymentCapture order={self.order_id} gateway={self.gateway} amount={self.amount}>"
