from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from datetime import datetime
import uuid

from app.core.database import Base


class DemandAlert(Base):
    __tablename__ = "demand_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_description = Column(String(500), nullable=False)
    city = Column(String(100), nullable=False, index=True)
    quantity_needed = Column(Numeric(15, 2))
    unit = Column(String(50))
    project_title = Column(String(500))
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    status = Column(String(50), default="pending", index=True)
    notified_vendors = Column(ARRAY(String))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    