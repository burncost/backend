"""Blog posts published from the admin console.

A published post is public on the marketing site (`/blog`, `/blog/{slug}`) and is
emailed to vendors/suppliers as a newsletter. Only admins may write here.

`status` is a simple string ("draft" | "published") rather than a DB enum: adding
a new editorial state later must not require an enum migration.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class BlogPost(Base):
    __tablename__ = "blog_posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    excerpt = Column(Text)
    content_html = Column(Text, nullable=False)
    cover_image_url = Column(Text)
    tags = Column(String(255))
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_name = Column(String(255), default="Burncost Editorial")
    status = Column(String(20), nullable=False, default="draft", index=True)
    published_at = Column(DateTime, index=True)
    # Filled in by the newsletter sender so "publish" is idempotent: a retry
    # never mails the same post twice.
    newsletter_sent_at = Column(DateTime)
    newsletter_recipient_count = Column(Integer, default=0)
    view_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    author = relationship("User")

    def __repr__(self) -> str:
        return f"<BlogPost {self.slug} ({self.status})>"
