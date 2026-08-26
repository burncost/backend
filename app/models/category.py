from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, Text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, backref
from datetime import datetime
import uuid

from app.core.database import Base


class Category(Base):
    __tablename__ = "categories"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"))
    description = Column(Text)
    image_url = Column(Text)
    is_active = Column(Boolean, default=True, index=True)
    display_order = Column(Integer, default=0)
    
    # --- Taxonomy expansion for BOQ hierarchy ---
    # Top-level grouping: "Structure", "Finishes", "MEP", "External Works", etc.
    division = Column(String(100), index=True)
    # Material type: "material", "labour", "equipment", "consumable"
    material_type = Column(String(50), default="material")
    # Default unit of measure for this category (e.g., "bag", "tonne", "meter", "piece")
    default_unit = Column(String(50))
    # Waste factor percentage for BOQ calculations (e.g., 5.00 = 5%)
    waste_factor = Column(Numeric(5, 2), default=0.00)
    # Burncost platform margin percentage per category (e.g., 10.00 = 10%)
    platform_margin = Column(Numeric(5, 2), default=5.00)
    # Fee model: "percentage", "fixed", or "service"
    fee_model = Column(String(20), default="percentage")
    # Fixed fee amount in Naira (for fixed-fee categories like Cement at ₦200/bag)
    fee_fixed = Column(Numeric(12, 2), default=None)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Self-referential relationship for hierarchy
    children = relationship("Category", backref=backref("parent", remote_side=[id]))
