"""Per-campaign recipient rows - the frozen send log.

The audience is resolved once (when the campaign is queued) and written here, so
what was mailed is auditable even if a vendor later changes tier/state or opts
out. `status` drives the retry loop:

    pending -> sending -> sent | failed        (failed rows are retryable)
    pending -> skipped                          (opt-out / no email / dry run)

`(campaign_id, email)` is unique so resolving the same audience twice can never
queue a duplicate email for one campaign. The string status avoids enum
migrations when a new outcome is added.
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class NewsletterRecipient(Base):
    __tablename__ = "newsletter_recipients"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "email", name="uq_newsletter_recipients_campaign_email"
        ),
        # The sender claims `pending` rows per campaign; this index keeps that
        # claim cheap even for a 5,000-recipient campaign.
        Index("ix_newsletter_recipients_campaign_status", "campaign_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id = Column(
        UUID(as_uuid=True),
        ForeignKey("newsletter_campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email = Column(String(255), nullable=False, index=True)
    # Merge-tag values captured at resolve time.
    name = Column(String(255))
    business_name = Column(String(255))
    city = Column(String(255))
    state = Column(String(255))
    tier = Column(String(20))
    # One-click unsubscribe token, frozen with the audience so a send never has
    # to re-read (or create) preferences mid-batch. Blank for pasted addresses.
    unsubscribe_token = Column(String(64))
    vendor_id = Column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )
    # pending | sending | sent | failed | skipped.
    status = Column(String(20), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    error = Column(Text)
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    campaign = relationship("NewsletterCampaign", back_populates="recipients")

    def __repr__(self) -> str:
        return f"<NewsletterRecipient {self.email} ({self.status})>"
