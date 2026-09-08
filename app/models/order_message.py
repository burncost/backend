"""Buyer↔vendor order messaging (Phase 4B).

A lightweight, per-order message thread so a buyer and the vendor(s) fulfilling
that order can discuss directly (clarifications, scheduling, notes). Additive:
lives entirely in its own table.

- Thread is implicit: all messages share `order_id`.
- `sender_role` records buyer | vendor | admin so UIs can label/allow actions.
- `read` is a per-message flag toggled by the receiving side (mark-all-read).
"""
from sqlalchemy import Column, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class OrderMessage(Base):
    __tablename__ = "order_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sender_role = Column(String(20), nullable=False)  # buyer | vendor | admin
    body = Column(Text, nullable=False)
    read = Column(Boolean, default=False, nullable=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    order = relationship("Order")

    def __repr__(self) -> str:
        return f"<OrderMessage id={self.id} order={self.order_id} role={self.sender_role}>"
