"""Admin newsletter console: campaign authoring, audience preview, send control.

The service layer (`newsletter_campaign_service`) owns audience resolution,
queueing and send bookkeeping; this module is only the HTTP shell around it:

  * `NewsletterError` -> 400 (something the admin can fix in the payload) and
    `CampaignStateError` -> 409 (the campaign moved on; re-read it first).
  * Read endpoints never write. `preview` resolves the audience with
    `create_tokens=False`, so clicking through segments cannot mint unsubscribe
    tokens as a side effect.
  * "Send now" freezes the audience inside the request
    (`schedule_campaign(when=None)` -> `queue_campaign`), so a campaign can never
    sit `queued` with no recipients for the sender to close as failed.
  * Nothing here talks to the email provider - the Celery sender (Phase 4)
    claims queued rows - so a transport failure can never half-write a queue.

Mounted under `/admin` (see `router.py`), guarded by the same roles as the blog
and promo consoles.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.config import settings
from app.core.database import get_db
from app.models.newsletter_campaign import NewsletterCampaign
from app.models.newsletter_recipient import NewsletterRecipient
from app.models.newsletter_template import NewsletterTemplate
from app.models.user import User
from app.services import newsletter_campaign_service as campaigns
from app.services.blog_service import sanitize_html
from app.services.newsletter_render import MERGE_TAGS, render_email

logger = logging.getLogger(__name__)
router = APIRouter()

# admin / super_admin are always allowed by require_roles.
admin_guard = require_roles("marketing", "manager")

CAMPAIGN_STATUSES = frozenset(
    {"draft", "scheduled", "queued", "sending", "sent", "failed", "cancelled"}
)
# A campaign with any delivered email is audit history, not a row to delete.
DELETABLE_STATUSES = frozenset({"draft", "cancelled", "failed"})
TEMPLATE_AUDIENCES = frozenset({"vendors", "customers", "custom"})


# ── request bodies ────────────────────────────────────────────────────────────


class CampaignCreate(BaseModel):
    name: str = Field("", max_length=255)
    subject: str = Field(..., min_length=1, max_length=500)
    body_html: str = Field(..., min_length=1)
    body_text: Optional[str] = None
    segment: str = campaigns.SEGMENT_VENDORS_ALL
    segment_params: Optional[Dict[str, Any]] = None
    scheduled_at: Optional[datetime] = Field(
        None, description="Omit to keep the draft; the beat picks due campaigns up"
    )
    dry_run: bool = Field(False, description="Resolve and log the audience, send nothing")
    template_id: Optional[UUID] = None


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    body_html: Optional[str] = None
    body_text: Optional[str] = None
    segment: Optional[str] = None
    segment_params: Optional[Dict[str, Any]] = None
    scheduled_at: Optional[datetime] = None
    dry_run: Optional[bool] = None


class ScheduleRequest(BaseModel):
    scheduled_at: Optional[datetime] = Field(
        None, description="Omit or null to send now (freezes the audience immediately)"
    )


class PreviewRequest(BaseModel):
    """A stand-in recipient, so the admin sees real merge values, not `{{tags}}`."""

    email: str = "preview@burncost.com"
    first_name: str = "Ada"
    last_name: str = "Okafor"
    business_name: str = "Ada Building Supplies"
    city: str = "Lagos"
    state: str = "Lagos"
    tier: str = "verified_vendor"


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    subject: str = Field("", max_length=500)
    body_html: str = ""
    body_text: Optional[str] = None
    audience: str = "vendors"
    is_active: bool = True


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    subject: Optional[str] = Field(None, max_length=500)
    body_html: Optional[str] = None
    body_text: Optional[str] = None
    audience: Optional[str] = None
    is_active: Optional[bool] = None


def _http_error(exc: campaigns.NewsletterError) -> HTTPException:
    """Payload problems are 400; a moved-on campaign is a 409."""
    code = (
        status.HTTP_409_CONFLICT
        if isinstance(exc, campaigns.CampaignStateError)
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail=str(exc))


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalise to the DB's naive-UTC timestamps (the columns are `DateTime`).

    An aware datetime from the console would make asyncpg raise on the
    `scheduled_at <= now()` comparison the scheduler runs.
    """
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def _get_campaign(db: AsyncSession, campaign_id: UUID) -> NewsletterCampaign:
    campaign = await db.get(NewsletterCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found"
        )
    return campaign


async def _get_template(db: AsyncSession, template_id: UUID) -> NewsletterTemplate:
    template = await db.get(NewsletterTemplate, template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
        )
    return template


def _clean_audience(raw: Optional[str]) -> str:
    audience = (raw or "vendors").strip().lower()
    if audience not in TEMPLATE_AUDIENCES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown audience '{raw}'. Use one of: "
                f"{', '.join(sorted(TEMPLATE_AUDIENCES))}"
            ),
        )
    return audience


def _template_to_dict(template: NewsletterTemplate) -> Dict[str, Any]:
    return {
        "id": str(template.id),
        "name": template.name,
        "slug": template.slug,
        "subject": template.subject or "",
        "body_html": template.body_html or "",
        "body_text": template.body_text or "",
        "audience": template.audience,
        "is_active": bool(template.is_active),
        "created_at": template.created_at.isoformat() if template.created_at else None,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None,
    }


# ── catalogue: segments, merge tags, limits ───────────────────────────────────


@router.get("/newsletters/segments")
async def list_segments(current_user: User = Depends(admin_guard)):
    """Everything the composer needs to build a campaign, in one call."""
    return {
        "segments": [
            {"id": key, "label": meta["label"], "params": meta["params"]}
            for key, meta in campaigns.SEGMENTS.items()
        ],
        "merge_tags": MERGE_TAGS,
        "limits": {
            "max_recipients": int(settings.NEWSLETTER_MAX_RECIPIENTS),
            "batch_size": int(settings.NEWSLETTER_BATCH_SIZE),
            "max_send_attempts": int(settings.NEWSLETTER_MAX_SEND_ATTEMPTS),
        },
    }


# ── templates ─────────────────────────────────────────────────────────────────
# Static paths are declared before `/newsletters/{campaign_id}` so a UUID path
# param can never swallow "templates" (FastAPI validates; it does not fall back).


@router.get("/newsletters/templates")
async def list_templates(
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(NewsletterTemplate).order_by(
            NewsletterTemplate.is_active.desc(), NewsletterTemplate.updated_at.desc()
        )
    )).scalars().all()
    return {"templates": [_template_to_dict(t) for t in rows]}


@router.post("/newsletters/templates", status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: TemplateCreate,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    body = sanitize_html(payload.body_html or "")
    if (payload.body_html or "").strip() and not body.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Template body is empty after sanitising",
        )
    template = NewsletterTemplate(
        name=" ".join(payload.name.split())[:255],
        subject=" ".join((payload.subject or "").split())[:500],
        body_html=body,
        body_text=(payload.body_text or "").strip() or None,
        audience=_clean_audience(payload.audience),
        is_active=bool(payload.is_active),
        created_by=getattr(current_user, "id", None),
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return _template_to_dict(template)


@router.patch("/newsletters/templates/{template_id}")
async def update_template(
    template_id: UUID,
    payload: TemplateUpdate,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    template = await _get_template(db, template_id)
    data = payload.model_dump(exclude_unset=True)

    if "name" in data:
        template.name = " ".join((data["name"] or "").split())[:255]
    if "subject" in data:
        template.subject = " ".join((data["subject"] or "").split())[:500]
    if "body_html" in data:
        raw = data["body_html"] or ""
        cleaned = sanitize_html(raw)
        if raw.strip() and not cleaned.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Template body is empty after sanitising",
            )
        template.body_html = cleaned
    if "body_text" in data:
        template.body_text = (data["body_text"] or "").strip() or None
    if "audience" in data:
        template.audience = _clean_audience(data["audience"])
    if "is_active" in data:
        template.is_active = bool(data["is_active"])

    await db.commit()
    await db.refresh(template)
    return _template_to_dict(template)


@router.delete("/newsletters/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: UUID,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Delete a template. Campaigns keep their own copied subject/body."""
    template = await _get_template(db, template_id)
    await db.delete(template)
    await db.commit()



# ── campaigns ─────────────────────────────────────────────────────────────────


@router.get("/newsletters")
async def list_campaigns(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    query = select(NewsletterCampaign)
    if status_filter in CAMPAIGN_STATUSES:
        query = query.where(NewsletterCampaign.status == status_filter)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(
            or_(NewsletterCampaign.subject.ilike(like), NewsletterCampaign.name.ilike(like))
        )

    total = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar() or 0
    rows = (await db.execute(
        query.order_by(NewsletterCampaign.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()

    return {
        "campaigns": [campaigns.campaign_to_dict(c, include_body=False) for c in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


@router.post("/newsletters", status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreate,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Create a draft (or scheduled) campaign. Nothing is mailed yet."""
    try:
        campaign = await campaigns.create_campaign(
            db,
            created_by=getattr(current_user, "id", None),
            subject=payload.subject,
            body_html=payload.body_html,
            name=payload.name,
            body_text=payload.body_text,
            template_id=payload.template_id,
            segment=payload.segment,
            segment_params=payload.segment_params,
            scheduled_at=_naive_utc(payload.scheduled_at),
            dry_run=payload.dry_run,
        )
    except campaigns.NewsletterError as exc:
        raise _http_error(exc) from exc
    await db.commit()
    await db.refresh(campaign)
    return campaigns.campaign_to_dict(campaign)


@router.get("/newsletters/{campaign_id}")
async def get_campaign(
    campaign_id: UUID,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    campaign = await _get_campaign(db, campaign_id)
    return campaigns.campaign_to_dict(campaign)


@router.patch("/newsletters/{campaign_id}")
async def update_campaign(
    campaign_id: UUID,
    payload: CampaignUpdate,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    campaign = await _get_campaign(db, campaign_id)
    changes = payload.model_dump(exclude_unset=True)
    if "scheduled_at" in changes:
        changes["scheduled_at"] = _naive_utc(changes["scheduled_at"])
    try:
        campaign = await campaigns.update_campaign(db, campaign, changes)
    except campaigns.NewsletterError as exc:
        raise _http_error(exc) from exc
    await db.commit()
    await db.refresh(campaign)
    return campaigns.campaign_to_dict(campaign)


@router.delete("/newsletters/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    campaign_id: UUID,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Delete an unsent campaign. A sent campaign is the send log - cancel instead."""
    campaign = await _get_campaign(db, campaign_id)
    if campaign.status not in DELETABLE_STATUSES or int(campaign.sent_count or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Campaign is '{campaign.status}' with {int(campaign.sent_count or 0)} "
                "sent - cancel it instead of deleting its send log"
            ),
        )
    await db.delete(campaign)
    await db.commit()



# ── audience, preview, send control ──────────────────────────────────────────

RECIPIENT_STATUSES = frozenset({"pending", "sending", "sent", "failed", "skipped"})


@router.get("/newsletters/{campaign_id}/audience")
async def preview_campaign_audience(
    campaign_id: UUID,
    sample: int = Query(10, ge=0, le=50),
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Count + sample for the campaign's segment. Writes nothing (no tokens)."""
    campaign = await _get_campaign(db, campaign_id)
    try:
        return await campaigns.preview_audience(
            db, campaign.segment, campaign.segment_params, sample=sample
        )
    except campaigns.NewsletterError as exc:
        raise _http_error(exc) from exc


@router.post("/newsletters/{campaign_id}/preview")
async def preview_campaign(
    campaign_id: UUID,
    payload: Optional[PreviewRequest] = None,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Render the email for a stand-in recipient, exactly as a send would."""
    campaign = await _get_campaign(db, campaign_id)
    sample = payload or PreviewRequest()
    recipient = {
        "email": sample.email,
        "name": " ".join(p for p in (sample.first_name, sample.last_name) if p).strip(),
        "first_name": sample.first_name,
        "last_name": sample.last_name,
        "business_name": sample.business_name,
        "city": sample.city,
        "state": sample.state,
        "tier": sample.tier,
        # No token: a preview must not ship a dead unsubscribe link, and it must
        # not create a preference row for a non-recipient.
        "unsubscribe_token": "",
    }
    price_items = await campaigns.price_of_day_items(db)
    rendered = render_email(
        campaign,
        recipient,
        base_url=campaigns.base_url(),
        price_items=price_items,
    )
    audience = await campaigns.preview_audience(
        db, campaign.segment, campaign.segment_params
    )
    return {
        "subject": rendered.subject,
        "html": rendered.html,
        "text": rendered.text,
        "audience": audience,
    }


@router.post("/newsletters/{campaign_id}/schedule")
async def schedule_campaign(
    campaign_id: UUID,
    payload: Optional[ScheduleRequest] = None,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Schedule for later, or send now (no `scheduled_at`).

    Send-now freezes the audience in this transaction, so a worker can never
    pick up a queued campaign that has no recipients.
    """
    campaign = await _get_campaign(db, campaign_id)
    when = _naive_utc(payload.scheduled_at if payload else None)
    try:
        campaign = await campaigns.schedule_campaign(db, campaign, when)
    except campaigns.NewsletterError as exc:
        raise _http_error(exc) from exc
    await db.commit()
    await db.refresh(campaign)
    return campaigns.campaign_to_dict(campaign)


@router.post("/newsletters/{campaign_id}/cancel")
async def cancel_campaign(
    campaign_id: UUID,
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Stop a scheduled/queued campaign; rows not yet sent are marked skipped."""
    campaign = await _get_campaign(db, campaign_id)
    try:
        campaign = await campaigns.cancel_campaign(db, campaign)
    except campaigns.NewsletterError as exc:
        raise _http_error(exc) from exc
    await db.commit()
    await db.refresh(campaign)
    return campaigns.campaign_to_dict(campaign)


@router.get("/newsletters/{campaign_id}/recipients")
async def list_campaign_recipients(
    campaign_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_filter: Optional[str] = Query(None, alias="status"),
    current_user: User = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """The frozen send log for one campaign (what was mailed, to whom, why not)."""
    campaign = await _get_campaign(db, campaign_id)
    query = select(NewsletterRecipient).where(
        NewsletterRecipient.campaign_id == campaign.id
    )
    if status_filter in RECIPIENT_STATUSES:
        query = query.where(NewsletterRecipient.status == status_filter)

    total = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar() or 0
    rows = (await db.execute(
        query.order_by(NewsletterRecipient.created_at, NewsletterRecipient.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    counts = {
        row[0]: int(row[1])
        for row in (await db.execute(
            select(NewsletterRecipient.status, func.count())
            .where(NewsletterRecipient.campaign_id == campaign.id)
            .group_by(NewsletterRecipient.status)
        )).all()
    }

    return {
        "recipients": [campaigns.recipient_to_dict(r) for r in rows],
        "counts": counts,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }

