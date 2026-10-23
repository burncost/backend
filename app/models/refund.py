"""
Refund records - the payout side of a dispute resolution.

The published policy requires a refund to be actioned within one business day
of a decision. No gateway refund call is implemented yet, so a decision records
an auditable `Refund` row (`pending`) that the admin escrow/settlement screen
marks as `paid` once the money has been sent. This keeps the decision, the
amount and the payout reference together instead of only flipping order flags.
"""
from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

from app.core.database import Base


class Refund(Base):
    """A refund instructed against an order (usually by a dispute resolution)."""

    __tablename__ = "refunds"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    dispute_id = Column(
        UUID(as_uuid=True), ForeignKey("disputes.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    amount = Column(Numeric(15, 2), nullable=False, default=0)
    reason = Column(Text)
    gateway = Column(String(30))                       # paystack / flutterwave / manual
    # pending -> instructed, awaiting payout · paid -> money sent · void -> cancelled
    status = Column(String(20), default="pending", nullable=False, index=True)
    requested_by = Column(String(100))
    requested_at = Column(DateTime, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime)
    processed_by = Column(String(100))
    payout_reference = Column(String(100))
