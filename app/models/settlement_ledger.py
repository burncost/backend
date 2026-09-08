"""Vendor settlement ledger (Phase 5 — Settlement & Disbursement, increment 1).

A per-vendor, per-delivery accounting row tracking what Burncost *should* settle
to a vendor and why. This is the idempotent data foundation the payout runner
consumes later. It is deliberately INERT in this increment: nothing writes to it
automatically, no disbursement is executed, and no live payment/order flow is
touched. Accrual + Flutterwave/Monnify disbursement are built and validated on
staging behind the `SETTLEMENT_ENABLED` flag.

- One row per order (unique) so a vendor can never be settled twice for the same
  order (dedupe / idempotency).
- `gross_amount` = captured amount; `gateway_fee` + `commission_amount` reduce it
  to `net_amount`, which is what the vendor should be paid.
- `status`: pending | paid | cancelled (payout automation lands in a later
  increment, always after staging validation with test accounts).
"""
from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class VendorSettlementLedger(Base):
    __tablename__ = "vendor_settlement_ledger"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    period = Column(String(7), nullable=False, index=True)  # YYYY-MM settlement window

    gross_amount = Column(Numeric(15, 2), nullable=False)
    gateway_fee = Column(Numeric(15, 2), default=0, nullable=False)
    commission_amount = Column(Numeric(15, 2), default=0, nullable=False)
    net_amount = Column(Numeric(15, 2), nullable=False)

    # pending | paid | cancelled
    status = Column(String(20), default="pending", nullable=False)
    paid_at = Column(DateTime)
    payout_reference = Column(String(100))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vendor = relationship("Vendor")
    order = relationship("Order")

    def __repr__(self) -> str:
        return f"<VendorSettlementLedger vendor={self.vendor_id} net={self.net_amount} status={self.status}>"
