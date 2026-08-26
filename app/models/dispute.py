"""
Disputes & Refunds models — power the Dispute Resolution admin pages (Phase 5).
"""
from sqlalchemy import Column, String, DateTime, Numeric, Boolean, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class Dispute(Base):
    """A transaction dispute between a buyer and supplier."""
    __tablename__ = "disputes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dispute_number = Column(String(50), unique=True, nullable=False, index=True)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    dispute_type = Column(String(100), nullable=False)  # Product Quality, Delivery Delay, Refund, Payment Issue
    status = Column(String(30), default="open", index=True)  # open / in_review / resolved / escalated
    priority = Column(String(20), default="medium")  # low / medium / high / critical
    description = Column(Text)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    buyer_name = Column(String(255))
    supplier_name = Column(String(255))
    order_number = Column(String(50))
    amount = Column(Numeric(15, 2), default=0)
    filed_by = Column(String(100))
    filed_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime)
    resolved_by = Column(String(100))

    evidence = relationship("DisputeEvidence", back_populates="dispute", cascade="all, delete-orphan")
    resolutions = relationship("DisputeResolution", back_populates="dispute", cascade="all, delete-orphan")
    timeline = relationship("DisputeTimeline", back_populates="dispute", cascade="all, delete-orphan")


class DisputeEvidence(Base):
    """Evidence submitted by either party in a dispute."""
    __tablename__ = "dispute_evidence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dispute_id = Column(UUID(as_uuid=True), ForeignKey("disputes.id", ondelete="CASCADE"), nullable=False, index=True)
    submitted_by = Column(String(20), default="buyer")  # buyer / supplier
    evidence_type = Column(String(100))
    description = Column(Text)
    url = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    dispute = relationship("Dispute", back_populates="evidence")


class DisputeResolution(Base):
    """A resolution decision on a dispute."""
    __tablename__ = "dispute_resolutions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dispute_id = Column(UUID(as_uuid=True), ForeignKey("disputes.id", ondelete="CASCADE"), nullable=False, index=True)
    resolution_type = Column(String(50), default="full_refund_buyer")  # full_refund_buyer / no_refund_supplier / partial_split
    amount_refunded = Column(Numeric(15, 2), default=0)
    amount_released = Column(Numeric(15, 2), default=0)
    notes = Column(Text)
    decided_by = Column(String(100))
    decided_at = Column(DateTime, default=datetime.utcnow)

    dispute = relationship("Dispute", back_populates="resolutions")


class DisputeTimeline(Base):
    """Timeline events for a dispute."""
    __tablename__ = "dispute_timeline"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dispute_id = Column(UUID(as_uuid=True), ForeignKey("disputes.id", ondelete="CASCADE"), nullable=False, index=True)
    event = Column(String(255))
    description = Column(Text)
    actor = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    dispute = relationship("Dispute", back_populates="timeline")