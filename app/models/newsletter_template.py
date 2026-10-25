"""Reusable newsletter templates written in the admin console.

A template holds the subject/body an admin starts a campaign from. Merge tags
(`{{first_name}}`, `{{business_name}}`, `{{price_table}}`, ...) stay as literal
text here and are resolved at render time, so editing a template never rewrites
history - a sent campaign stores its own rendered subject/body.

`body_html` is sanitised on write (see `blog_service.sanitize_html`), and both
`audience` and `is_active` are plain columns rather than DB enums so a new
template kind never needs an enum migration.
"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class NewsletterTemplate(Base):
    __tablename__ = "newsletter_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    # Optional stable handle for built-in quick picks (e.g. "price-of-day").
    slug = Column(String(120), unique=True, nullable=True, index=True)
    subject = Column(String(500), nullable=False, default="")
    body_html = Column(Text, nullable=False, default="")
    body_text = Column(Text)
    # Who the copy was written for: "vendors" | "customers" | "custom".
    audience = Column(String(20), nullable=False, default="vendors", index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User")

    def __repr__(self) -> str:
        return f"<NewsletterTemplate {self.name} ({self.audience})>"
