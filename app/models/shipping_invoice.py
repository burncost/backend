"""ShippingInvoice model - the post-confirmation delivery payment for an order.

Delivery is deliberately NOT part of checkout: goods, fees and VAT are paid first,
then the buyer pays the delivery arrangement (vendor quote or accepted driver bid)
in a separate transaction, mirroring `TokenPurchase`:

- the row is written when the gateway checkout is created and is the single source
  of truth for the amount,
- `reference` is also the gateway tx_ref / paymentReference, so a webhook can map a
  charge back to this invoice,
- a reference that is already `completed` is never applied twice (idempotency).

One invoice per payment attempt. An order can have several over its life: a later
vendor group that becomes ready after the first delivery was paid produces a new
delta invoice that only covers the still-unpaid jobs.
"""
from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.core.database import Base

INVOICE_PENDING = "pending"
INVOICE_COMPLETED = "completed"
INVOICE_FAILED = "failed"


class ShippingInvoice(Base):
    __tablename__ = "shipping_invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Our own reference (SHP-xxxxxxxxxxxx) - also the gateway transaction reference.
    reference = Column(String(100), nullable=False, unique=True, index=True)

    # Server-computed amount (sum of accepted, unpaid delivery jobs at creation).
    amount = Column(Numeric(15, 2), nullable=False)
    paid_amount = Column(Numeric(15, 2), nullable=True)  # amount the gateway reported

    provider = Column(String(20), nullable=True)             # flutterwave | paystack | monnify | mock
    status = Column(String(20), nullable=False, default=INVOICE_PENDING, index=True)
    provider_reference = Column(String(100), nullable=True)  # gateway transaction id

    # Snapshot of the exact jobs this invoice pays for (list of UUID strings).
    # A later job (or a re-quote) must never be silently covered by an older
    # invoice, so settlement only releases these ids - and only if they are still
    # unpaid and their total still equals `amount`.
    covered_job_ids = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    order = relationship("Order")

    def __repr__(self) -> str:
        return f"<ShippingInvoice ref={self.reference} amount={self.amount} status={self.status}>"
