from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.core.database import Base


class PriceTrend(str, enum.Enum):
    UP = "up"
    DOWN = "down"
    STABLE = "stable"


class MaterialRate(Base):
    """Stores current market rates for materials by state/region."""
    __tablename__ = "material_rates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False, index=True)
    material_name = Column(String(255), nullable=False)
    specification = Column(String(500))  # e.g., "42.5R", "12mm", "20L"
    unit = Column(String(50), nullable=False)
    current_price = Column(Numeric(15, 2), nullable=False)
    previous_price = Column(Numeric(15, 2))
    currency = Column(String(10), default="NGN")
    state = Column(String(100), index=True)
    lga = Column(String(100))
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id"))
    trend = Column(SQLEnum(PriceTrend, native_enum=False, length=10,
                           values_callable=lambda e: [m.value for m in e]),
                   default=PriceTrend.STABLE)
    source = Column(String(50), default="manual")  # "manual", "api", "scraped"
    verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = relationship("Category")
    supplier = relationship("Vendor")

    def __repr__(self):
        return f"<MaterialRate {self.material_name} - {self.state}: {self.current_price}>"


class MaterialRateHistory(Base):
    """Price history for trend analysis."""
    __tablename__ = "material_rate_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rate_id = Column(UUID(as_uuid=True), ForeignKey("material_rates.id", ondelete="CASCADE"), nullable=False, index=True)
    price = Column(Numeric(15, 2), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    source = Column(String(50))

    rate = relationship("MaterialRate", backref="price_history")
