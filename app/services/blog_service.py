"""Blog authoring helpers: slugging, HTML sanitisation and serialisation.

Admin-authored HTML is untrusted input: it is rendered on the public site and in
newsletter emails, so it is sanitised server-side with an allowlist. We use the
standard library (`html.parser`) because the project does not ship a dedicated
sanitiser (no bleach/nh3) and adding a dependency for one field is not warranted.

Policy:
  * Unknown tags are dropped but their text is kept (so `<span>hi</span>` -> `hi`).
  * Script/style/iframe/object/embed are dropped WITH their contents.
  * Only a small attribute allowlist survives, and `href`/`src` must be http(s),
    mailto, or a site-relative path - `javascript:` and `data:` URLs never pass.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blog_post import BlogPost

ALLOWED_TAGS = {
    "p", "br", "hr", "h2", "h3", "h4", "strong", "b", "em", "i", "u", "s",
    "ul", "ol", "li", "blockquote", "a", "img", "figure", "figcaption",
    "table", "thead", "tbody", "tr", "th", "td", "code", "pre", "span", "div",
}
VOID_TAGS = {"br", "hr", "img"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "form", "input", "link", "meta"}
ALLOWED_ATTRS = {
    "a": {"href", "title", "target", "rel"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}


def _safe_url(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate:
        return False
    lowered = candidate.lower()
    if lowered.startswith(("http://", "https://", "mailto:", "/", "#")):
        return True
    # A bare relative path ("blog/post") is fine; a scheme we do not allow is not.
    return ":" not in candidate


class _Sanitizer(HTMLParser):
    """Allowlist HTML sanitiser (text preserved, dangerous blocks removed)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.drop_depth = 0

    def _attrs(self, tag: str, attrs) -> str:
        allowed = ALLOWED_ATTRS.get(tag, set())
        parts: list[str] = []
        for name, value in attrs:
            key = (name or "").lower()
            if key not in allowed or value is None:
                continue
            if key in {"href", "src"} and not _safe_url(value):
                continue
            if key == "target" and value not in {"_blank", "_self"}:
                continue
            parts.append(f' {key}="{html.escape(value, quote=True)}"')
            if key == "target" and value == "_blank" and "rel" not in {p.strip().split("=")[0] for p in parts}:
                parts.append(' rel="noopener noreferrer"')
        return "".join(parts)

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.drop_depth += 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.drop_depth = max(0, self.drop_depth - 1)
            return
        if self.drop_depth or tag not in ALLOWED_TAGS or tag not in self.stack:
            return
        # Close anything left open inside this element, then the element itself.
        while self.stack:
            open_tag = self.stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.drop_depth or not data:
            return
        self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(raw: Optional[str]) -> str:
    """Return safe HTML for public rendering. Empty input yields an empty string."""
    if not raw:
        return ""
    parser = _Sanitizer()
    parser.feed(raw)
    parser.close()
    return parser.result().strip()


def slugify(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return text[:80] or "post"


async def unique_slug(
    db: AsyncSession, title: str, exclude_id: Optional[UUID] = None
) -> str:
    base = slugify(title)
    candidate = base
    suffix = 2
    while True:
        query = select(BlogPost.id).where(BlogPost.slug == candidate)
        if exclude_id is not None:
            query = query.where(BlogPost.id != exclude_id)
        if (await db.execute(query)).scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1
        if suffix > 200:
            return f"{base}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


def post_to_dict(post: BlogPost, *, include_content: bool = True) -> dict:
    data = {
        "id": str(post.id),
        "title": post.title,
        "slug": post.slug,
        "excerpt": post.excerpt or "",
        "cover_image_url": post.cover_image_url,
        "tags": [t.strip() for t in (post.tags or "").split(",") if t.strip()],
        "author_name": post.author_name or "Burncost Editorial",
        "status": post.status,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "newsletter_sent_at": post.newsletter_sent_at.isoformat() if post.newsletter_sent_at else None,
        "newsletter_recipient_count": int(post.newsletter_recipient_count or 0),
        "view_count": int(post.view_count or 0),
        "created_at": post.created_at.isoformat() if post.created_at else None,
        "updated_at": post.updated_at.isoformat() if post.updated_at else None,
    }
    if include_content:
        data["content_html"] = post.content_html
    return data
