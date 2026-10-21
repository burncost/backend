from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from app.core.database import Base

# Demand alerts used to be time-boxed: an alert was listed for this many hours
# after it was raised, then it disappeared from every listing. That rule is
# retired - an alert now stays open until a buyer and a vendor agree
# (`status="agreed"`) or an admin closes it. The 48h cutoff is frozen at the
# release timestamp so rows raised before the change keep their historical
# "expired" state and nothing raised afterwards can expire.
DEMAND_ALERT_TTL_HOURS = 48
DEMAND_ALERT_TTL_FROZEN_AT = datetime(2026, 10, 24, tzinfo=timezone.utc)


def demand_alert_cutoff(now: Optional[datetime] = None) -> datetime:
    """Oldest `created_at` that is still visible under the legacy 48h rule.

    `min(now - 48h, freeze - 48h)`: the cutoff stops advancing at the freeze
    timestamp, so pre-existing stale alerts stay expired while new alerts never
    do. Returned timezone-aware UTC so it compares against the DB's
    `timestamp with time zone` column.
    """
    rolling = (now or datetime.now(timezone.utc)) - timedelta(hours=DEMAND_ALERT_TTL_HOURS)
    frozen = DEMAND_ALERT_TTL_FROZEN_AT - timedelta(hours=DEMAND_ALERT_TTL_HOURS)
    return min(rolling, frozen)



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

    # ── Per-item take-off identity and agreement (additive + nullable) ────────
    # `dedupe_key` keeps one open alert per (builder, city, material) so a BOQ
    # regeneration cannot fan the same line out twice. `agreed_*` record the
    # buyer-vendor agreement that closes the broadcast; an alert expires only
    # when one is reached (or an admin closes it).
    material_key = Column(String(100), nullable=True, index=True)
    spec = Column(String(255), nullable=True)
    source_ref = Column(String(255), nullable=True)
    dedupe_key = Column(String(255), nullable=True, index=True)
    agreed_vendor_id = Column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agreed_price = Column(Numeric(15, 2), nullable=True)
    agreed_at = Column(DateTime(timezone=True), nullable=True)
    agreed_by = Column(String(20), nullable=True)  # builder / supplier (who accepted)

    