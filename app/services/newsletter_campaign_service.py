"""Newsletter campaign audience, queueing and send bookkeeping.

Three boundaries are deliberate:
  * Services `flush()`; the caller (admin endpoint or Celery task) commits. A
    send batch therefore commits exactly once, after the provider answered.
  * Nothing here talks to the email provider - that stays in the task module, so
    transport failures cannot leave a half-written queue.
  * The audience is FROZEN into `newsletter_recipients` when a campaign is
    queued. Later tier/state changes or opt-outs do not rewrite it; each row
    carries its own merge values and unsubscribe token.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.email_preference import EmailPreference
from app.models.newsletter_campaign import NewsletterCampaign
from app.models.newsletter_recipient import NewsletterRecipient
from app.models.user import User, UserProfile, UserRole, UserStatus
from app.models.vendor import Vendor, VendorVerificationStatus
from app.services.blog_service import sanitize_html
from app.services.newsletter_service import NewsletterService

logger = logging.getLogger(__name__)

# Segment ids double as the stored `campaign.segment` value.
SEGMENT_VENDORS_ALL = "vendors_all"
SEGMENT_VENDORS_VERIFIED = "vendors_verified"
SEGMENT_VENDORS_BY_STATE = "vendors_by_state"
SEGMENT_VENDORS_BY_TIER = "vendors_by_tier"
SEGMENT_MARKETING_OPT_IN = "marketing_opt_in"
SEGMENT_CUSTOM_EMAILS = "custom_emails"

SEGMENTS: Dict[str, Dict[str, Any]] = {
    SEGMENT_VENDORS_ALL: {"label": "All active vendors", "params": []},
    SEGMENT_VENDORS_VERIFIED: {"label": "Verified vendors only", "params": []},
    SEGMENT_VENDORS_BY_STATE: {"label": "Vendors in one state", "params": ["state"]},
    SEGMENT_VENDORS_BY_TIER: {"label": "Vendors by verification tier", "params": ["tier"]},
    SEGMENT_MARKETING_OPT_IN: {"label": "Marketing opt-ins (explicit consent)", "params": []},
    SEGMENT_CUSTOM_EMAILS: {"label": "Pasted address list", "params": ["emails"]},
}

VENDOR_TIERS = ("starter", "verified_vendor", "enterprise")
_VENDOR_SEGMENTS = frozenset(
    {SEGMENT_VENDORS_ALL, SEGMENT_VENDORS_VERIFIED, SEGMENT_VENDORS_BY_STATE, SEGMENT_VENDORS_BY_TIER}
)
ACTIVE_USER_STATUSES = (UserStatus.ACTIVE, UserStatus.PENDING_VERIFICATION)
# Statuses whose content can still be changed. Anything queued is a promise to
# the recipients already written down.
EDITABLE_STATUSES = frozenset({"draft", "scheduled", "cancelled", "failed"})
IN_PROGRESS_STATUSES = frozenset({"queued", "sending"})
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ERROR_MAX = 1000

_SEGMENT_FIELDS = (
    "segment",
    "segment_params",
    "subject",
    "body_html",
    "body_text",
    "name",
    "scheduled_at",
    "dry_run",
)


class NewsletterError(Exception):
    """Admin-fixable campaign problem (mapped to HTTP 400 by the API layer)."""


class AudienceTooLarge(NewsletterError):
    """The resolved audience exceeds NEWSLETTER_MAX_RECIPIENTS."""


class EmptyAudience(NewsletterError):
    """The segment resolved to nobody; queueing would send nothing."""


class CampaignStateError(NewsletterError):
    """The campaign is not in a state where this action is allowed."""


def base_url() -> str:
    """Marketing site root used for links and unsubscribe URLs."""
    return (getattr(settings, "FRONTEND_URL", None) or "https://burncost.com").rstrip("/")


def normalize_emails(raw: Any) -> List[str]:
    """Lower-case, validate and de-duplicate a pasted address list (order kept)."""
    if isinstance(raw, str):
        candidates = re.split(r"[\s,;]+", raw)
    elif isinstance(raw, Iterable):
        candidates = [str(item) for item in raw]
    else:
        return []
    seen: set = set()
    out: List[str] = []
    for candidate in candidates:
        email = (candidate or "").strip().lower()
        if not email or email in seen:
            continue
        if not _EMAIL_RE.match(email):
            raise NewsletterError(f"'{candidate.strip()}' is not a valid email address")
        seen.add(email)
        out.append(email)
    return out


def validate_segment(
    segment: Optional[str], params: Optional[Mapping[str, Any]]
) -> tuple[str, Dict[str, Any]]:
    """Normalise a segment id + its params, or raise `NewsletterError`."""
    key = (segment or SEGMENT_VENDORS_ALL).strip().lower()
    if key not in SEGMENTS:
        raise NewsletterError(
            f"Unknown segment '{segment}'. Known segments: {', '.join(sorted(SEGMENTS))}"
        )

    raw = dict(params or {})
    clean: Dict[str, Any] = {}
    if key == SEGMENT_VENDORS_BY_STATE:
        state = str(raw.get("state") or "").strip()
        if not state:
            raise NewsletterError("This segment needs a 'state'")
        clean["state"] = state[:255]
    elif key == SEGMENT_VENDORS_BY_TIER:
        tier = str(raw.get("tier") or "").strip().lower()
        if tier not in VENDOR_TIERS:
            raise NewsletterError(f"Tier must be one of: {', '.join(VENDOR_TIERS)}")
        clean["tier"] = tier
    elif key == SEGMENT_CUSTOM_EMAILS:
        emails = normalize_emails(raw.get("emails"))
        if not emails:
            raise NewsletterError("Paste at least one email address")
        clean["emails"] = emails
    return key, clean


def _suppressed(pref: Optional[EmailPreference]) -> bool:
    """A hard opt-out is `opted_out_at` set while consent is absent.

    Mirrors `NewsletterService.vendor_supplier_recipients`: a plain False
    `marketing_opt_in` is the default, not a request to never be contacted.
    """
    return pref is not None and pref.opted_out_at is not None and not pref.marketing_opt_in


def _vendor_stmt(*, verified: bool = False, state: Optional[str] = None, tier: Optional[str] = None):
    """Vendor rows joined to their user + profile, with merge values inline."""
    stmt = (
        select(
            User.id.label("user_id"),
            User.email.label("email"),
            UserProfile.first_name,
            UserProfile.last_name,
            UserProfile.business_name.label("profile_business_name"),
            Vendor.id.label("vendor_id"),
            Vendor.business_name.label("vendor_business_name"),
            Vendor.city,
            Vendor.state,
            Vendor.verification_tier,
        )
        .join(Vendor, Vendor.user_id == User.id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(
            User.role == UserRole.VENDOR,
            User.status.in_(ACTIVE_USER_STATUSES),
            User.email.isnot(None),
        )
    )
    if verified:
        stmt = stmt.where(Vendor.verification_status == VendorVerificationStatus.VERIFIED)
    if state:
        stmt = stmt.where(func.lower(Vendor.state) == state.lower())
    if tier:
        stmt = stmt.where(Vendor.verification_tier == tier)
    return stmt


async def _prefs_by_user(
    db: AsyncSession, user_ids: Sequence[UUID]
) -> Dict[UUID, EmailPreference]:
    if not user_ids:
        return {}
    rows = (await db.execute(
        select(EmailPreference).where(EmailPreference.user_id.in_(list(user_ids)))
    )).scalars().all()
    return {pref.user_id: pref for pref in rows}


async def _prefs_by_email(db: AsyncSession, emails: Sequence[str]) -> Dict[str, EmailPreference]:
    if not emails:
        return {}
    rows = (await db.execute(
        select(EmailPreference).where(func.lower(EmailPreference.email).in_(list(emails)))
    )).scalars().all()
    return {(pref.email or "").strip().lower(): pref for pref in rows}


def _vendor_recipient(row, token: str = "") -> Dict[str, Any]:
    """One vendor row -> audience dict (all merge values are pre-trimmed)."""
    m = row._mapping
    name = " ".join(part for part in (m["first_name"], m["last_name"]) if part).strip()
    return {
        "user_id": m["user_id"],
        "vendor_id": m["vendor_id"],
        "email": (m["email"] or "").strip().lower(),
        "name": name,
        "first_name": (m["first_name"] or "").strip(),
        "last_name": (m["last_name"] or "").strip(),
        "business_name": (m["vendor_business_name"] or m["profile_business_name"] or "").strip(),
        "city": (m["city"] or "").strip(),
        "state": (m["state"] or "").strip(),
        "tier": (m["verification_tier"] or "").strip(),
        "unsubscribe_token": token,
    }


def _dedupe(recipients: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the first row per lower-cased address (audience order preserved)."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for recipient in recipients:
        email = (recipient.get("email") or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        recipient["email"] = email
        out.append(recipient)
    return out


async def resolve_audience(
    db: AsyncSession,
    segment: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    create_tokens: bool = True,
) -> List[Dict[str, Any]]:
    """Resolve a segment to the exact recipient rows a campaign would mail.

    Hard opt-outs are always skipped. `create_tokens=False` is for previews: it
    never writes a preference row, so an admin poking at segments cannot mint
    unsubscribe tokens as a side effect.
    """
    segment, params = validate_segment(segment, params)
    recipients: List[Dict[str, Any]] = []

    if segment in _VENDOR_SEGMENTS:
        rows = (await db.execute(_vendor_stmt(
            verified=segment == SEGMENT_VENDORS_VERIFIED,
            state=params.get("state"),
            tier=params.get("tier"),
        ))).all()
        prefs = await _prefs_by_user(db, [row._mapping["user_id"] for row in rows])
        # Mint missing tokens in one flush: a 5,000-recipient campaign must not
        # cost 5,000 round trips. The resulting rows match `_token_for`'s rule
        # (marketing consent stays off; the token is what the unsubscribe
        # endpoint looks up).
        new_prefs: List[EmailPreference] = []
        for row in rows:
            user_id = row._mapping["user_id"]
            pref = prefs.get(user_id)
            if _suppressed(pref):
                continue
            token = (pref.unsubscribe_token if pref is not None and pref.unsubscribe_token else "")
            if not token and create_tokens:
                token = uuid4().hex
                new_prefs.append(EmailPreference(
                    user_id=user_id,
                    email=(row._mapping["email"] or "").strip().lower(),
                    marketing_opt_in=False,
                    unsubscribe_token=token,
                ))
            recipients.append(_vendor_recipient(row, token))
        if new_prefs:
            db.add_all(new_prefs)
            await db.flush()

    elif segment == SEGMENT_MARKETING_OPT_IN:
        rows = (await db.execute(
            select(
                EmailPreference.user_id,
                EmailPreference.email,
                EmailPreference.unsubscribe_token,
                UserProfile.first_name,
                UserProfile.last_name,
                UserProfile.business_name.label("profile_business_name"),
                Vendor.id.label("vendor_id"),
                Vendor.business_name.label("vendor_business_name"),
                Vendor.city,
                Vendor.state,
                Vendor.verification_tier,
            )
            .outerjoin(User, User.id == EmailPreference.user_id)
            .outerjoin(UserProfile, UserProfile.user_id == EmailPreference.user_id)
            .outerjoin(Vendor, Vendor.user_id == EmailPreference.user_id)
            .where(
                EmailPreference.marketing_opt_in.is_(True),
                EmailPreference.email.isnot(None),
            )
        )).all()
        for row in rows:
            recipients.append(_vendor_recipient(row, row._mapping["unsubscribe_token"] or ""))

    else:  # SEGMENT_CUSTOM_EMAILS - pasted addresses, may have no user account
        emails = params["emails"]
        prefs = await _prefs_by_email(db, emails)
        for email in emails:
            pref = prefs.get(email)
            if _suppressed(pref):
                continue
            token = (pref.unsubscribe_token if pref is not None and pref.unsubscribe_token else "")
            recipients.append({"email": email, "unsubscribe_token": token})

    recipients = _dedupe(recipients)
    cap = int(getattr(settings, "NEWSLETTER_MAX_RECIPIENTS", 5000))
    if len(recipients) > cap:
        raise AudienceTooLarge(
            f"This segment resolves to {len(recipients)} recipients, above the {cap} per-campaign cap"
        )
    return recipients


async def preview_audience(
    db: AsyncSession, segment: str, params: Optional[Mapping[str, Any]] = None, *, sample: int = 5
) -> Dict[str, Any]:
    """Count + a small sample, for the admin console's segment picker."""
    recipients = await resolve_audience(db, segment, params, create_tokens=False)
    return {
        "segment": segment,
        "segment_params": dict(params or {}),
        "count": len(recipients),
        "max_recipients": int(getattr(settings, "NEWSLETTER_MAX_RECIPIENTS", 5000)),
        "sample": [
            {
                "email": r["email"],
                "name": r.get("name") or "",
                "business_name": r.get("business_name") or "",
                "state": r.get("state") or "",
            }
            for r in recipients[: max(0, sample)]
        ],
    }


def _clean_subject(raw: Optional[str]) -> str:
    subject = " ".join((raw or "").split())
    if not subject:
        raise NewsletterError("A subject is required")
    return subject[:500]


def _clean_body_html(raw: Optional[str]) -> str:
    """Allowlist-sanitised body (same policy as blog content).

    Sanitising drops inline `style`/`class` attributes; the email shell in
    `newsletter_render.wrap_html` supplies the typography, so campaigns stay
    on-brand without trusting admin-supplied attributes in an inbox.
    """
    body = sanitize_html(raw)
    if not body.strip():
        raise NewsletterError("A body is required")
    return body


def _clean_body_text(raw: Optional[str]) -> Optional[str]:
    text = (raw or "").strip()
    return text or None


def _ensure_editable(campaign: NewsletterCampaign) -> None:
    if campaign.status not in EDITABLE_STATUSES:
        raise CampaignStateError(
            f"Campaign is '{campaign.status}' - only draft, scheduled, failed or "
            "cancelled campaigns can be edited"
        )


async def create_campaign(
    db: AsyncSession,
    *,
    created_by: Optional[UUID],
    subject: str,
    body_html: str,
    name: str = "",
    body_text: Optional[str] = None,
    template_id: Optional[UUID] = None,
    segment: str = SEGMENT_VENDORS_ALL,
    segment_params: Optional[Mapping[str, Any]] = None,
    scheduled_at: Optional[datetime] = None,
    dry_run: bool = False,
    blog_post_id: Optional[UUID] = None,
) -> NewsletterCampaign:
    """Create a draft (or scheduled) campaign. Caller commits."""
    segment, params = validate_segment(segment, segment_params)
    campaign = NewsletterCampaign(
        name=" ".join((name or "").split())[:255],
        subject=_clean_subject(subject),
        body_html=_clean_body_html(body_html),
        body_text=_clean_body_text(body_text),
        template_id=template_id,
        segment=segment,
        segment_params=params,
        status="scheduled" if scheduled_at else "draft",
        scheduled_at=scheduled_at,
        dry_run=bool(dry_run),
        blog_post_id=blog_post_id,
        created_by=created_by,
        total_recipients=0,
        sent_count=0,
        failed_count=0,
        skipped_count=0,
    )
    db.add(campaign)
    await db.flush()
    await db.refresh(campaign)
    return campaign


async def update_campaign(
    db: AsyncSession, campaign: NewsletterCampaign, changes: Mapping[str, Any]
) -> NewsletterCampaign:
    """Patch an editable campaign. Unknown keys are rejected (API whitelists)."""
    _ensure_editable(campaign)
    unknown = set(changes) - set(_SEGMENT_FIELDS)
    if unknown:
        raise NewsletterError(f"Unknown campaign field(s): {', '.join(sorted(unknown))}")

    if "name" in changes:
        campaign.name = " ".join((changes["name"] or "").split())[:255]
    if "subject" in changes:
        campaign.subject = _clean_subject(changes["subject"])
    if "body_html" in changes:
        campaign.body_html = _clean_body_html(changes["body_html"])
    if "body_text" in changes:
        campaign.body_text = _clean_body_text(changes["body_text"])
    if "dry_run" in changes:
        campaign.dry_run = bool(changes["dry_run"])
    if "segment" in changes or "segment_params" in changes:
        segment, params = validate_segment(
            changes.get("segment", campaign.segment),
            changes.get("segment_params", campaign.segment_params),
        )
        campaign.segment = segment
        campaign.segment_params = params
    if "scheduled_at" in changes:
        campaign.scheduled_at = changes["scheduled_at"]
        # A rescheduled draft becomes due; clearing the time returns it to draft.
        if campaign.scheduled_at and campaign.status in {"draft", "cancelled"}:
            campaign.status = "scheduled"
        elif not campaign.scheduled_at and campaign.status == "scheduled":
            campaign.status = "draft"

    await db.flush()
    await db.refresh(campaign)
    return campaign


async def schedule_campaign(
    db: AsyncSession, campaign: NewsletterCampaign, when: Optional[datetime]
) -> NewsletterCampaign:
    """Send now (when=None) or at `when`.

    "Send now" freezes the audience immediately, so a campaign can never sit in
    `queued` with no recipients (which the sender would close as failed).
    """
    _ensure_editable(campaign)
    campaign.scheduled_at = when
    if when is None:
        campaign.status = "draft"
        await db.flush()
        await queue_campaign(db, campaign)
        return campaign
    campaign.status = "scheduled"
    await db.flush()
    return campaign


async def cancel_campaign(db: AsyncSession, campaign: NewsletterCampaign) -> NewsletterCampaign:
    """Stop a campaign before it finishes; rows not yet sent are marked skipped."""
    if campaign.status == "sent":
        raise CampaignStateError("A sent campaign cannot be cancelled")
    if campaign.status == "cancelled":
        return campaign
    campaign.status = "cancelled"
    campaign.scheduled_at = None
    campaign.finished_at = datetime.utcnow()
    await db.execute(
        update(NewsletterRecipient)
        .where(
            NewsletterRecipient.campaign_id == campaign.id,
            NewsletterRecipient.status.in_(("pending", "sending")),
        )
        .values(status="skipped", error="campaign cancelled")
    )
    await db.flush()
    return campaign


async def queue_campaign(db: AsyncSession, campaign: NewsletterCampaign) -> Dict[str, int]:
    """Freeze the audience into recipient rows and mark the campaign queued.

    Re-queueing (a retry, or flipping a dry run to a real send) is safe:
      * rows for addresses no longer in the segment are deleted, so an opt-out
        between runs is honoured;
      * failed/skipped rows for addresses still in the segment return to pending
        with a fresh attempt budget.
    """
    if campaign.status in IN_PROGRESS_STATUSES:
        raise CampaignStateError("This campaign is already queued")
    if campaign.status == "sent":
        raise CampaignStateError("A sent campaign cannot be re-queued")

    audience = await resolve_audience(db, campaign.segment, campaign.segment_params or {})
    if not audience:
        raise EmptyAudience("This segment resolves to no recipients")
    emails = [row["email"] for row in audience]

    await db.execute(
        delete(NewsletterRecipient).where(
            NewsletterRecipient.campaign_id == campaign.id,
            NewsletterRecipient.email.notin_(emails),
            # Already-sent rows are the audit log; never delete them, even when
            # the segment has since narrowed.
            NewsletterRecipient.status != "sent",
        )
    )
    await db.execute(
        update(NewsletterRecipient)
        .where(
            NewsletterRecipient.campaign_id == campaign.id,
            NewsletterRecipient.status.in_(("failed", "skipped")),
        )
        .values(status="pending", attempts=0, error=None, sent_at=None)
    )
    existing = {
        (row or "").strip().lower()
        for row in (await db.execute(
            select(NewsletterRecipient.email).where(
                NewsletterRecipient.campaign_id == campaign.id
            )
        )).scalars().all()
    }
    fresh = [
        NewsletterRecipient(
            campaign_id=campaign.id,
            email=row["email"],
            name=row.get("name") or None,
            business_name=row.get("business_name") or None,
            city=row.get("city") or None,
            state=row.get("state") or None,
            tier=row.get("tier") or None,
            unsubscribe_token=row.get("unsubscribe_token") or None,
            vendor_id=row.get("vendor_id"),
            status="pending",
            attempts=0,
        )
        for row in audience
        if row["email"] not in existing
    ]
    for start in range(0, len(fresh), 500):
        db.add_all(fresh[start:start + 500])
        await db.flush()

    total = int((await db.execute(
        select(func.count()).select_from(NewsletterRecipient).where(
            NewsletterRecipient.campaign_id == campaign.id
        )
    )).scalar_one())
    campaign.total_recipients = total
    campaign.status = "queued"
    campaign.finished_at = None
    campaign.last_error = None
    await db.flush()
    return {"queued": len(fresh), "total": total}


async def claim_batch(
    db: AsyncSession, campaign_id: UUID, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Atomically move up to `limit` pending rows to `sending` and return them.

    `FOR UPDATE SKIP LOCKED` lets two workers (or overlapping Celery beats) run
    side by side without ever claiming the same recipient.
    """
    batch = int(limit or getattr(settings, "NEWSLETTER_BATCH_SIZE", 50))
    ids = (
        select(NewsletterRecipient.id)
        .where(
            NewsletterRecipient.campaign_id == campaign_id,
            NewsletterRecipient.status == "pending",
        )
        .order_by(NewsletterRecipient.created_at, NewsletterRecipient.id)
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    rows = (await db.execute(
        update(NewsletterRecipient)
        .where(NewsletterRecipient.id.in_(ids))
        .values(
            status="sending",
            attempts=NewsletterRecipient.attempts + 1,
            error=None,
        )
        .returning(
            NewsletterRecipient.id,
            NewsletterRecipient.email,
            NewsletterRecipient.name,
            NewsletterRecipient.business_name,
            NewsletterRecipient.city,
            NewsletterRecipient.state,
            NewsletterRecipient.tier,
            NewsletterRecipient.unsubscribe_token,
            NewsletterRecipient.attempts,
        )
    )).all()
    await db.flush()
    return [dict(row._mapping) for row in rows]


async def mark_sending(db: AsyncSession, campaign: NewsletterCampaign) -> NewsletterCampaign:
    """Flip a queued campaign to `sending` (idempotent while in progress)."""
    if campaign.status not in {"queued", "sending"}:
        raise CampaignStateError(f"Campaign is '{campaign.status}', not queued")
    if campaign.status == "queued":
        campaign.status = "sending"
        campaign.started_at = campaign.started_at or datetime.utcnow()
        await db.flush()
    return campaign


async def record_sent(db: AsyncSession, recipient_id: UUID) -> None:
    """Mark a claimed row delivered.

    Only a `sending` row is updated: a cancel (or an opt-out) that landed while
    the message was in flight already released the row, and that verdict stands.
    """
    await db.execute(
        update(NewsletterRecipient)
        .where(
            NewsletterRecipient.id == recipient_id,
            NewsletterRecipient.status == "sending",
        )
        .values(status="sent", sent_at=datetime.utcnow(), error=None)
    )
    await db.flush()


async def record_failure(
    db: AsyncSession, recipient_id: UUID, error: Optional[str], *, attempts: int
) -> str:
    """Return the row to `pending` while attempts remain, else mark it `failed`.

    Like `record_sent`, a row a cancel already released is left alone.
    """
    give_up = attempts >= int(getattr(settings, "NEWSLETTER_MAX_SEND_ATTEMPTS", 3))
    status = "failed" if give_up else "pending"
    await db.execute(
        update(NewsletterRecipient)
        .where(
            NewsletterRecipient.id == recipient_id,
            NewsletterRecipient.status == "sending",
        )
        .values(status=status, error=(error or "send failed")[:_ERROR_MAX])
    )
    await db.flush()
    return status


async def mark_skipped(db: AsyncSession, recipient_id: UUID, reason: str) -> None:
    await db.execute(
        update(NewsletterRecipient)
        .where(NewsletterRecipient.id == recipient_id)
        .values(status="skipped", error=(reason or "skipped")[:_ERROR_MAX])
    )
    await db.flush()


async def refresh_counts(db: AsyncSession, campaign: NewsletterCampaign) -> Dict[str, int]:
    """Recompute the campaign counters from its recipient rows."""
    rows = (await db.execute(
        select(NewsletterRecipient.status, func.count())
        .where(NewsletterRecipient.campaign_id == campaign.id)
        .group_by(NewsletterRecipient.status)
    )).all()
    counts = Counter({status: int(count) for status, count in rows})
    campaign.total_recipients = sum(counts.values())
    campaign.sent_count = counts.get("sent", 0)
    campaign.failed_count = counts.get("failed", 0)
    campaign.skipped_count = counts.get("skipped", 0)
    await db.flush()
    return {
        "pending": counts.get("pending", 0),
        "sending": counts.get("sending", 0),
        "sent": campaign.sent_count,
        "failed": campaign.failed_count,
        "skipped": campaign.skipped_count,
        "total": campaign.total_recipients,
    }


async def finalize_campaign(db: AsyncSession, campaign: NewsletterCampaign) -> Dict[str, int]:
    """Close the campaign once nothing is pending. Safe to call repeatedly."""
    counts = await refresh_counts(db, campaign)
    if counts["pending"] or counts["sending"]:
        return counts
    if campaign.status == "cancelled":
        return counts
    campaign.finished_at = datetime.utcnow()
    if counts["sent"] or campaign.dry_run:
        campaign.status = "sent"
    else:
        campaign.status = "failed"
        campaign.last_error = (
            campaign.last_error or f"No email was delivered ({counts['failed']} failed)"
        )
    await db.flush()
    return counts


async def due_campaign_ids(
    db: AsyncSession, *, now: Optional[datetime] = None, limit: int = 20
) -> List[UUID]:
    """Ids of scheduled campaigns whose send time has arrived (FIFO)."""
    moment = now or datetime.utcnow()
    rows = (await db.execute(
        select(NewsletterCampaign.id)
        .where(
            NewsletterCampaign.status == "scheduled",
            NewsletterCampaign.scheduled_at.isnot(None),
            NewsletterCampaign.scheduled_at <= moment,
        )
        .order_by(NewsletterCampaign.scheduled_at, NewsletterCampaign.created_at)
        .limit(max(1, int(limit)))
    )).scalars().all()
    return list(rows)


async def price_of_day_items(db: AsyncSession, limit: int = 6) -> List[Dict[str, Any]]:
    """`{{price_table}}` data - the same source the legacy daily email uses."""
    try:
        return await NewsletterService().build_price_of_day(db, limit)
    except Exception as exc:  # a price outage must not block a campaign send
        logger.warning("newsletter: price table unavailable: %s", exc)
        return []


def campaign_to_dict(campaign: NewsletterCampaign, *, include_body: bool = True) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": str(campaign.id),
        "name": campaign.name or "",
        "subject": campaign.subject,
        "segment": campaign.segment,
        "segment_params": campaign.segment_params or {},
        "status": campaign.status,
        "dry_run": bool(campaign.dry_run),
        "template_id": str(campaign.template_id) if campaign.template_id else None,
        "blog_post_id": str(campaign.blog_post_id) if campaign.blog_post_id else None,
        "scheduled_at": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "total_recipients": int(campaign.total_recipients or 0),
        "sent_count": int(campaign.sent_count or 0),
        "failed_count": int(campaign.failed_count or 0),
        "skipped_count": int(campaign.skipped_count or 0),
        "last_error": campaign.last_error,
        "created_by": str(campaign.created_by) if campaign.created_by else None,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "updated_at": campaign.updated_at.isoformat() if campaign.updated_at else None,
    }
    if include_body:
        data["body_html"] = campaign.body_html
        data["body_text"] = campaign.body_text or ""
    return data


def recipient_to_dict(recipient: NewsletterRecipient) -> Dict[str, Any]:
    return {
        "id": str(recipient.id),
        "email": recipient.email,
        "name": recipient.name or "",
        "business_name": recipient.business_name or "",
        "state": recipient.state or "",
        "status": recipient.status,
        "attempts": int(recipient.attempts or 0),
        "error": recipient.error,
        "sent_at": recipient.sent_at.isoformat() if recipient.sent_at else None,
    }
