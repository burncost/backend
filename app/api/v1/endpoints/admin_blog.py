"""Admin blog console: create/edit posts, upload covers, publish to newsletter.

Publishing is the single action that (a) makes a post public and (b) queues the
vendor/supplier newsletter. The send runs as a background task, is idempotent
(`newsletter_sent_at` guard) and never rolls back a publish if email fails.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.config import settings
from app.core.database import get_db
from app.models.blog_post import BlogPost
from app.models.user import User
from app.services.blog_service import post_to_dict, sanitize_html, unique_slug
from app.services.cloudinary_upload_service import upload_image_to_cloudinary
from app.services.newsletter_service import NewsletterService

logger = logging.getLogger(__name__)
router = APIRouter()

admin_guard = require_roles("manager", "support", "marketing")


def _base_url() -> str:
    return (getattr(settings, "FRONTEND_URL", None) or "https://burncost.com").rstrip("/")


def _clean_slug(raw: Optional[str]) -> Optional[str]:
    """Validate an admin-supplied slug (it appears in public URLs and emails)."""
    if raw is None:
        return None
    slug = raw.strip().lower()
    if not slug:
        return None
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slug may only contain lowercase letters, numbers and single hyphens",
        )
    return slug[:255]


class BlogPostCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    content_html: str = Field(..., min_length=1)
    excerpt: Optional[str] = Field(None, max_length=600)
    cover_image_url: Optional[str] = None
    tags: Optional[str] = Field(None, max_length=255)
    author_name: Optional[str] = Field(None, max_length=255)
    slug: Optional[str] = Field(None, max_length=255)
    status: str = "draft"


class BlogPostUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=255)
    content_html: Optional[str] = None
    excerpt: Optional[str] = Field(None, max_length=600)
    cover_image_url: Optional[str] = None
    tags: Optional[str] = Field(None, max_length=255)
    author_name: Optional[str] = Field(None, max_length=255)
    slug: Optional[str] = Field(None, max_length=255)


@router.get("/blog")
async def list_blog_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    query = select(BlogPost)
    if status_filter in {"draft", "published"}:
        query = query.where(BlogPost.status == status_filter)
    if search:
        query = query.where(BlogPost.title.ilike(f"%{search}%"))

    total = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar() or 0
    rows = (await db.execute(
        query.order_by(BlogPost.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()

    return {
        "posts": [post_to_dict(p, include_content=False) for p in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


@router.get("/blog/{post_id}")
async def get_blog_post(
    post_id: UUID,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    post = await db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")
    return post_to_dict(post)


@router.post("/blog", status_code=status.HTTP_201_CREATED)
async def create_blog_post(
    payload: BlogPostCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    content = sanitize_html(payload.content_html)
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Post body is empty after sanitising")

    slug = _clean_slug(payload.slug) or await unique_slug(db, payload.title)
    if (await db.execute(select(BlogPost.id).where(BlogPost.slug == slug))).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already in use")

    publish = (payload.status or "draft").lower() == "published"
    post = BlogPost(
        title=payload.title.strip(),
        slug=slug,
        excerpt=(payload.excerpt or "").strip() or None,
        content_html=content,
        cover_image_url=payload.cover_image_url,
        tags=payload.tags,
        author_id=getattr(current_user, "id", None),
        author_name=(payload.author_name or "").strip() or None,
        status="published" if publish else "draft",
        published_at=datetime.utcnow() if publish else None,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    if publish:
        background_tasks.add_task(
            NewsletterService().send_blog_newsletter_for_post, str(post.id), _base_url()
        )
    return post_to_dict(post)



@router.put("/blog/{post_id}")
async def update_blog_post(
    post_id: UUID,
    payload: BlogPostUpdate,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    post = await db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")

    data = payload.dict(exclude_unset=True)
    if "content_html" in data:
        sanitized = sanitize_html(data["content_html"])
        if not sanitized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Post body is empty after sanitising")
        post.content_html = sanitized
    if "title" in data:
        post.title = data["title"].strip()
    if "excerpt" in data:
        post.excerpt = (data["excerpt"] or "").strip() or None
    if "cover_image_url" in data:
        post.cover_image_url = data["cover_image_url"]
    if "tags" in data:
        post.tags = data["tags"]
    if "author_name" in data:
        post.author_name = data["author_name"]
    if data.get("slug"):
        slug = _clean_slug(data["slug"])
        if not slug:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid slug")
        clash = (await db.execute(
            select(BlogPost.id).where(BlogPost.slug == slug, BlogPost.id != post.id)
        )).scalar_one_or_none()
        if clash:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already in use")
        post.slug = slug

    await db.commit()
    await db.refresh(post)
    return post_to_dict(post)


@router.post("/blog/{post_id}/publish")
async def publish_blog_post(
    post_id: UUID,
    background_tasks: BackgroundTasks,
    send_newsletter: bool = Query(True, description="Queue the vendor newsletter"),
    resend: bool = Query(False, description="Re-send even if already sent"),
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    post = await db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")

    post.status = "published"
    if post.published_at is None:
        post.published_at = datetime.utcnow()
    await db.commit()
    await db.refresh(post)

    queued = False
    if send_newsletter:
        background_tasks.add_task(
            NewsletterService().send_blog_newsletter_for_post,
            str(post.id), _base_url(), force=resend,
        )
        queued = True

    return {
        "post": post_to_dict(post, include_content=False),
        "newsletter_queued": queued,
        "message": "Published. Newsletter queued for vendors and suppliers." if queued else "Published.",
    }


@router.post("/blog/{post_id}/unpublish")
async def unpublish_blog_post(
    post_id: UUID,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    post = await db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")
    post.status = "draft"
    await db.commit()
    await db.refresh(post)
    return post_to_dict(post, include_content=False)


@router.delete("/blog/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blog_post(
    post_id: UUID,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    post = await db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")
    await db.delete(post)
    await db.commit()


@router.post("/blog/cover")
async def upload_blog_cover(
    file: UploadFile = File(...),
    current_user: User = Depends(admin_guard),
):
    allowed = {"image/jpeg", "image/png", "image/webp", "image/jpg"}
    if file.content_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG and WebP images are allowed",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload")
    if len(content) > 6 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image must be under 6 MB")
    try:
        url = await upload_image_to_cloudinary(content)
        return {"url": url}
    except Exception as exc:
        logger.error("Blog cover upload failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Image upload failed. Please try again.",
        )
