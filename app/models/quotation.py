"""Quotation — user-uploaded supplier quotations parsed for verification."""
from sqlalchemy import (
    Column, String, DateTime, Numeric, Integer, ForeignKey, Text, Boolean, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class Quotation(Base):
    """Top-level supplier quotation submitted by a user for verification."""
    __tablename__ = "quotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quotation_number = Column(String(50), unique=True, nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    supplier_name = Column(String(255))
    city = Column(String(100), nullable=True, index=True)
    currency = Column(String(10), default="NGN")
    status = Column(String(20), default="pending")  # pending / verified / flagged / reviewed
    total_quoted = Column(Numeric(15, 2), default=0)
    total_market = Column(Numeric(15, 2), default=0)
    total_overcharge = Column(Numeric(15, 2), default=0)
    inflated_count = Column(Integer, default=0)
    fair_count = Column(Integer, default=0)
    unverified_count = Column(Integer, default=0)

    # Provenance / trust
    price_source = Column(String(20), default="database")  # database / ai_estimate / unavailable
    demand_alerts_created = Column(Integer, default=0)

    raw_text = Column(Text)
    source_filename = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship("QuotationLineItem", back_populates="quotation", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_quotations_user_created", "user_id", "created_at"),)


class QuotationLineItem(Base):
    """Line item within a quotation, with DB-verified market comparison."""
    __tablename__ = "quotation_line_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quotation_id = Column(UUID(as_uuid=True), ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True)

    description = Column(String(500), nullable=False)
    quantity = Column(Numeric(15, 2), default=0)
    unit = Column(String(50))
    quoted_rate = Column(Numeric(15, 2), default=0)
    quoted_amount = Column(Numeric(15, 2), default=0)

    market_rate = Column(Numeric(15, 2))
    price_source = Column(String(20), default="database")
    verified = Column(Boolean, default=False)
    confidence = Column(Numeric(5, 2), default=0)
    deviation_pct = Column(Numeric(5, 2))
    status = Column(String(20), default="unverified")  # fair / inflated / unverified

    quotation = relationship("Quotation", back_populates="items")

    __table_args__ = (Index("ix_quotation_line_items_qid", "quotation_id"),)