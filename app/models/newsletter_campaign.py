"""Newsletter campaigns (draft -> scheduled/queued -> sending -> sent/failed).

One row is one send. The rendered subject/body are copied onto the campaign so a
later template edit cannot change what was already mailed, and the audience is
frozen into `newsletter_recipients` rows before the first email goes out - that
frozen list is the send log the admin console reads and retries from.

`status`, `segment` and `dry_run` are plain strings/booleans, not DB enums: new
segments and states must not require an enum migration. `scheduled_at` is claimed
atomically by the scheduler (`UPDATE ... WHERE status = 'scheduled' RETURNING`),
which is what keeps a due campaign from being mailed twice.
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class NewsletterCampaign(Base):
    __tablename__ = "newsletter_campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, default="")
    subject = Column(String(500), nullable=False)
    template_id = Column(
        UUID(as_uuid=True),
        ForeignKey("newsletter_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    body_html = Column(Text, nullable=False)
    body_text = Column(Text)
    # vendors_all | vendors_verified | vendors_by_state | vendors_by_tier |
    # marketing_opt_in | custom_emails (see newsletter_campaign_service).
    segment = Column(String(50), nullable=False, default="vendors_all", index=True)
    # Segment arguments, e.g. {"state": "Lagos"} or {"emails": [...]}.
    segment_params = Column(JSONB)
    # draft | scheduled | queued | sending | sent | failed | cancelled.
    status = Column(String(20), nullable=False, default="draft", index=True)
    # A dry run resolves and logs the audience but never calls the provider.
    dry_run = Column(Boolean, nullable=False, default=False)
    scheduled_at = Column(DateTime, index=True)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    total_recipients = Column(Integer, nullable=False, default=0)
    sent_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    # Set when the campaign was created from a published blog post ("from blog").
    blog_post_id = Column(
        UUID(as_uuid=True),
        ForeignKey("blog_posts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Reason the last send attempt failed (provider error, bad segment, ...).
    last_error = Column(Text)
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    template = relationship("NewsletterTemplate")
    blog_post = relationship("BlogPost")
    creator = relationship("User")
    recipients = relationship(
        "NewsletterRecipient",
        back_populates="campaign",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<NewsletterCampaign {self.name} ({self.status})>"
