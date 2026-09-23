from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from app.core.database import Base

# Demand alerts are time-boxed: an alert is only listed for this many hours after it
# was raised, then it disappears from every listing (vendor + public).
# Single source of truth — the listing endpoints must not hard-code the window.
DEMAND_ALERT_TTL_HOURS = 48


def demand_alert_cutoff(now: Optional[datetime] = None) -> datetime:
    """Oldest `created_at` that is still visible; anything older has expired.

    Returned as a timezone-aware UTC datetime so it compares correctly against the
    `timestamp with time zone` column (the DB default is now()).
    """
    return (now or datetime.now(timezone.utc)) - timedelta(hours=DEMAND_ALERT_TTL_HOURS)


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
    