from sqlalchemy import Column, String, Enum as SQLEnum, DateTime, Numeric, Integer, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.core.database import Base


class ProductStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    OUT_OF_STOCK = "out_of_stock"
    DISCONTINUED = "discontinued"
    PENDING_APPROVAL = "pending_approval"


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False)
    brand_id = Column(UUID(as_uuid=True), ForeignKey("brands.id"))
    name = Column(String(500), nullable=False, index=True)
    slug = Column(String(500), unique=True, nullable=False, index=True)
    sku = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text)
    short_description = Column(Text)
    status = Column(
        SQLEnum(ProductStatus, native_enum=False, length=20),
        default=ProductStatus.DRAFT,
        index=True
    )
    base_price = Column(Numeric(15, 2), nullable=False)
    discount_price = Column(Numeric(15, 2))
    discount_percentage = Column(Numeric(5, 2))
    cost_price = Column(Numeric(15, 2))
    quantity = Column(Integer, default=0)
    low_stock_threshold = Column(Integer, default=10)
    allow_backorder = Column(Boolean, default=False)
    weight = Column(Numeric(10, 2))
    length = Column(Numeric(10, 2))
    width = Column(Numeric(10, 2))
    height = Column(Numeric(10, 2))
    unit_of_measure = Column(String(50), default="piece")
    minimum_order_quantity = Column(Integer, default=1)
    meta_title = Column(String(255))
    meta_description = Column(Text)
    meta_keywords = Column(Text)
    view_count = Column(Integer, default=0)
    sales_count = Column(Integer, default=0)
    rating = Column(Numeric(3, 2), default=0.00)
    review_count = Column(Integer, default=0)
    is_featured = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    published_at = Column(DateTime)

    vendor = relationship("Vendor", back_populates="products")
    images = relationship("ProductImage", back_populates="product", cascade="all, delete-orphan")
    specifications = relationship("ProductSpecification", back_populates="product", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Product {self.name}>"


class ProductImage(Base):
    __tablename__ = "product_images"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    image_url = Column(Text, nullable=False)
    alt_text = Column(String(255))
    display_order = Column(Integer, default=0)
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="images")


class ProductSpecification(Base):
    __tablename__ = "product_specifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    spec_name = Column(String(255), nullable=False)
    spec_value = Column(Text, nullable=False)
    display_order = Column(Integer, default=0)

    product = relationship("Product", back_populates="specifications")


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    sku = Column(String(100), unique=True, nullable=False)
    variant_name = Column(String(255), nullable=False)
    price_adjustment = Column(Numeric(15, 2), default=0.00)
    quantity = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)