"""
Price Integrity + BOQ Analysis models — power the Price Integrity and
BOQ Analysis admin pages (Phase 4).
"""
from sqlalchemy import (
    Column, String, DateTime, Numeric, Integer, Boolean, ForeignKey, Text, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class PriceAnomaly(Base):
    """A flagged price variance between quoted and market price."""
    __tablename__ = "price_anomalies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    anomaly_number = Column(String(50), unique=True, nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id"), nullable=True)
    item_name = Column(String(500), nullable=False)
    supplier_name = Column(String(255))
    market_price = Column(Numeric(15, 2), default=0)
    quoted_price = Column(Numeric(15, 2), default=0)
    variance_pct = Column(Numeric(5, 2), default=0)
    status = Column(String(20), default="flagged")  # flagged / warning / normal / approved / rejected
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)
    reviewed_at = Column(DateTime)
    reviewed_by = Column(String(100))
    history = relationship("PriceAnomalyHistory", back_populates="anomaly", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_price_anomalies_status", "status"),)


class PriceAnomalyHistory(Base):
    """Historical price snapshots for an anomaly."""
    __tablename__ = "price_anomaly_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    anomaly_id = Column(UUID(as_uuid=True), ForeignKey("price_anomalies.id", ondelete="CASCADE"), nullable=False, index=True)
    price = Column(Numeric(15, 2), default=0)
    recorded_at = Column(DateTime, default=datetime.utcnow)

    anomaly = relationship("PriceAnomaly", back_populates="history")


class BOQAnalysis(Base):
    """Top-level AI BOQ analysis record."""
    __tablename__ = "boq_analyses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    boq_number = Column(String(50), unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    status = Column(String(50), default="completed")  # completed / processing / failed
    created_by = Column(String(255))
    version = Column(Integer, default=1)
    confidence = Column(Numeric(5, 2), default=0)
    total_items = Column(Integer, default=0)
    flagged_items = Column(Integer, default=0)
    total_value = Column(Numeric(15, 2), default=0)
    quoted_value = Column(Numeric(15, 2), default=0)
    potential_savings = Column(Numeric(15, 2), default=0)
    avg_variance = Column(Numeric(5, 2), default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    items = relationship("BOQAnalysisItem", back_populates="analysis", cascade="all, delete-orphan")


class BOQAnalysisItem(Base):
    """Line item within a BOQ analysis with quoted vs market pricing."""
    __tablename__ = "boq_analysis_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    boq_id = Column(UUID(as_uuid=True), ForeignKey("boq_analyses.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(100))
    item_name = Column(String(500), nullable=False)
    quantity = Column(Numeric(15, 2), default=0)
    quoted_price = Column(Numeric(15, 2), default=0)
    market_price = Column(Numeric(15, 2), default=0)
    variance_pct = Column(Numeric(5, 2), default=0)
    potential_saving = Column(Numeric(15, 2), default=0)
    status = Column(String(20), default="normal")  # flagged / warning / normal

    analysis = relationship("BOQAnalysis", back_populates="items")

    def __repr__(self):
        return f"<BOQAnalysisItem {self.item_name}>"


class BOQAnalysisFlag(Base):
    """An AI-recommendation flag raised on a BOQ item."""
    __tablename__ = "boq_analysis_flags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    boq_id = Column(UUID(as_uuid=True), ForeignKey("boq_analyses.id", ondelete="CASCADE"), nullable=False, index=True)
    item_id = Column(UUID(as_uuid=True), ForeignKey("boq_analysis_items.id", ondelete="CASCADE"), nullable=True)
    severity = Column(String(20), default="medium")  # high / medium / low
    issue = Column(Text)
    recommendation = Column(Text)
    status = Column(String(20), default="open")  # open / resolved
    created_at = Column(DateTime, default=datetime.utcnow)