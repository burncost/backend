"""User email marketing preferences (Phase 3 — newsletter / opt-in).

Stores explicit, revocable consent for marketing/newsletter email. This is the
compliance record the scheduled newsletter sender queries before emailing anyone:
a user must have `marketing_opt_in = True` to be contacted for marketing.

- One row per user (`user_id` unique).
- `marketing_opt_in` defaults False — emailing always requires an explicit opt-in.
- `unsubscribe_token` powers a one-click unsubscribe link (no login required).
- The actual sends reuse NotificationService/Brevo in a later sub-increment; this
  table only governs *who* may be emailed.
"""
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class EmailPreference(Base):
    __tablename__ = "user_email_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    email = Column(String(255), index=True, nullable=False)
    # Explicit consent for marketing/newsletter email (defaults to no consent).
    marketing_opt_in = Column(Boolean, default=False, nullable=False)
    opted_in_at = Column(DateTime)
    opted_out_at = Column(DateTime)
    # Random token for one-click unsubscribe links (no auth required).
    unsubscribe_token = Column(String(64), unique=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="email_preference")

    def __repr__(self) -> str:
        return f"<EmailPreference user={self.user_id} opt_in={self.marketing_opt_in}>"
