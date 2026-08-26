"""
Negotiation system models — supplier-builder discount negotiations.
Powers the Negotiation Center admin pages (Phase 2).
"""
from sqlalchemy import (
    Column, String, DateTime, Numeric, Integer, Boolean, ForeignKey, Text, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class Negotiation(Base):
    """A single supplier-builder discount negotiation request."""
    __tablename__ = "negotiations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    negotiation_number = Column(String(50), unique=True, nullable=False, index=True)
    builder_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=True)
    product_name = Column(String(500), nullable=False)
    category = Column(String(100))
    quantity = Column(Numeric(15, 2), default=1)
    unit = Column(String(50), default="piece")
    requested_discount = Column(Numeric(5, 2), nullable=False)
    counter_offer = Column(Numeric(5, 2), nullable=True)
    final_discount = Column(Numeric(5, 2), nullable=True)
    value = Column(Numeric(15, 2), default=0)
    status = Column(String(50), default="pending", index=True)  # pending/approved/rejected/auto_approved/auto_rejected/counter_offered/counter_accepted/counter_declined/expired
    admin_note = Column(Text)
    flagged = Column(Boolean, default=False)
    suspended = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    builder = relationship("User", foreign_keys=[builder_id])
    supplier = relationship("Vendor", foreign_keys=[supplier_id])
    counter_offers = relationship(
        "NegotiationCounterOffer",
        back_populates="negotiation",
        cascade="all, delete-orphan",
        order_by="NegotiationCounterOffer.created_at",
    )
    audit_entries = relationship(
        "NegotiationAuditEntry",
        back_populates="negotiation",
        cascade="all, delete-orphan",
        order_by="NegotiationAuditEntry.created_at",
    )

    __table_args__ = (
        Index("ix_negotiations_status_created", "status", "created_at"),
    )

    def __repr__(self):
        return f"<Negotiation {self.negotiation_number} {self.status}>"


class NegotiationCounterOffer(Base):
    """A counter-offer made by either party during a negotiation."""
    __tablename__ = "negotiation_counter_offers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    negotiation_id = Column(UUID(as_uuid=True), ForeignKey("negotiations.id", ondelete="CASCADE"), nullable=False, index=True)
    offered_by = Column(String(20), nullable=False)  # builder / supplier / admin
    discount_percent = Column(Numeric(5, 2), nullable=False)
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    negotiation = relationship("Negotiation", back_populates="counter_offers")

    def __repr__(self):
        return f"<NegotiationCounterOffer {self.offered_by} {self.discount_percent}%>"


class DiscountConfiguration(Base):
    """Per-supplier/product discount rules that control auto decisioning."""
    __tablename__ = "discount_configurations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    config_number = Column(String(50), unique=True, nullable=False, index=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=True)
    product_name = Column(String(500), nullable=False)
    category = Column(String(100))
    discount_enabled = Column(Boolean, default=True)
    max_discount_pct = Column(Numeric(5, 2), default=15)
    auto_approval_threshold = Column(Numeric(5, 2), default=5)
    auto_rejection_threshold = Column(Numeric(5, 2), default=18)
    min_order_qty = Column(Integer, default=1)
    min_order_value = Column(Numeric(15, 2), default=0)
    quote_expiration_hours = Column(Integer, default=48)
    last_modified_by = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    supplier = relationship("Vendor", foreign_keys=[supplier_id])

    def __repr__(self):
        return f"<DiscountConfiguration {self.config_number}>"


class NegotiationAuditEntry(Base):
    """Audit trail for all negotiation actions (admin overrides, auto rules)."""
    __tablename__ = "negotiation_audit_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    negotiation_id = Column(UUID(as_uuid=True), ForeignKey("negotiations.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(100), nullable=False)
    performed_by = Column(String(100))  # admin user or "system"
    prev_value = Column(String(255))
    new_value = Column(String(255))
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    negotiation = relationship("Negotiation", back_populates="audit_entries")

    def __repr__(self):
        return f"<NegotiationAuditEntry {self.action}>"