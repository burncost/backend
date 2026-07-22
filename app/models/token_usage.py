"""
TokenUsage model — tracks user token balance and consumption.
"""
from sqlalchemy import Column, String, Integer, Float, DateTime, Enum as SAEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
import enum

from app.core.database import Base


class TransactionType(str, enum.Enum):
    PURCHASE = "purchase"
    CONSUMPTION = "consumption"
    REFUND = "refund"
    FREE_TIER = "free_tier"
    EXPIRY = "expiry"


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Balance tracking
    balance = Column(Integer, default=0, nullable=False)
    lifetime_purchased = Column(Integer, default=0, nullable=False)
    lifetime_consumed = Column(Integer, default=0, nullable=False)

    # Monthly free tier tracking
    free_tier_used_this_month = Column(Integer, default=0, nullable=False)
    free_tier_month = Column(String(7), nullable=True)  # "2026-07"

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TokenTransaction(Base):
    __tablename__ = "token_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    transaction_type = Column(SAEnum(TransactionType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    amount = Column(Integer, nullable=False)  # positive for purchase, negative for consumption
    balance_after = Column(Integer, nullable=False)

    # Context
    action_type = Column(String(50), nullable=True)  # "boq_generate_manual", "export_pdf", etc.
    boq_id = Column(String(50), nullable=True)
    reference = Column(String(100), nullable=True)  # payment reference for purchases

    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
