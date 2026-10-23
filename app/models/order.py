from sqlalchemy import Column, String, Enum as SQLEnum, DateTime, Numeric, Integer, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.core.database import Base


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    PAYMENT_FAILED = "payment_failed"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    READY_FOR_PICKUP = "ready_for_pickup"
    # Canonical status once a driver has picked the parcel up (mirrors
    # delivery_jobs.picked_up). Kept alongside SHIPPED: SHIPPED is the legacy
    # value written before this step existed and still lives on historical rows,
    # so both remain valid and are treated as the same stage everywhere.
    PICKED_UP = "picked_up"
    SHIPPED = "shipped"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PaymentMethod(str, enum.Enum):
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    USSD = "ussd"
    WALLET = "wallet"
    PAY_ON_DELIVERY = "pay_on_delivery"


class Order(Base):
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_number = Column(String(50), unique=True, nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    status = Column(
        SQLEnum(OrderStatus, native_enum=False, length=20,
                values_callable=lambda e: [m.value for m in e]),
        default=OrderStatus.PENDING_PAYMENT,
        index=True
    )
    shipping_address_id = Column(UUID(as_uuid=True), ForeignKey("customer_addresses.id"))
    billing_address_id = Column(UUID(as_uuid=True), ForeignKey("customer_addresses.id"))
    subtotal = Column(Numeric(15, 2), nullable=False)
    shipping_fee = Column(Numeric(15, 2), default=0.00)
    tax_amount = Column(Numeric(15, 2), default=0.00)
    discount_amount = Column(Numeric(15, 2), default=0.00)
    total_amount = Column(Numeric(15, 2), nullable=False)
    payment_status = Column(
        SQLEnum(PaymentStatus, native_enum=False, length=25,
                values_callable=lambda e: [m.value for m in e]),
        default=PaymentStatus.PENDING
    )
    payment_method = Column(
        SQLEnum(PaymentMethod, native_enum=False, length=20,
                values_callable=lambda e: [m.value for m in e])
    )
    customer_notes = Column(Text)
    admin_notes = Column(Text)
    # Promo code applied at creation (counter bumped at order time, not payment),
    # so an abandoned draft can release the use when deleted/cancelled/expired.
    promo_code = Column(String(50))
    # Flat Naira promo amount actually applied to this order, persisted so the
    # checkout summary can itemise it (and reverse it) without re-evaluating the
    # code against the buyer's live order history.
    promo_discount = Column(Numeric(15, 2), default=0.00, server_default="0")
    driver_name = Column(String(255))
    driver_phone = Column(String(20))
    estimated_delivery_date = Column(DateTime)
    delivered_at = Column(DateTime)
    # ── Dispute-resolution spine (see Frontend/src/app/content/legal) ───────
    # Funds are held for `inspection_window_hours` counted from verified
    # delivery, then the Celery auto-release task settles the vendor lines.
    inspection_window_hours = Column(Integer, default=48, server_default="48")
    auto_release_at = Column(DateTime, index=True)
    # Stamped once the vendor lines have actually been queued/paid out. Also
    # the idempotency key for the auto-release task.
    escrow_released_at = Column(DateTime)
    # One buyer-requested extension per order (policy: +24h on request).
    inspection_extension_requested_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    shipping_address = relationship(
        "CustomerAddress",
        foreign_keys=[shipping_address_id],
        backref="orders_as_shipping"
    )
    billing_address = relationship(
        "CustomerAddress",
        foreign_keys=[billing_address_id],
        backref="orders_as_billing"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    variant_id = Column(UUID(as_uuid=True))
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id"), nullable=False, index=True)
    product_name = Column(String(500), nullable=False)
    sku = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(15, 2), nullable=False)
    total_price = Column(Numeric(15, 2), nullable=False)
    vendor_status = Column(
        SQLEnum(OrderStatus, native_enum=False, length=20,
                values_callable=lambda e: [m.value for m in e]),
        default=OrderStatus.CONFIRMED
    )
    vendor_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", backref="order_items")
    vendor = relationship("Vendor", backref="order_items")
    