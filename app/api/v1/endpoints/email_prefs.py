"""Email marketing preferences (Phase 3 - newsletter opt-in).

Lets a signed-in user view or change their explicit marketing opt-in, and lets
anyone unsubscribe via a one-click token link (no login required). No email is
sent here - this only records consent that the later newsletter sender respects.
"""
from datetime import datetime
from uuid import UUID
import os
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status as http_status, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.email_preference import EmailPreference
from app.models.newsletter_recipient import NewsletterRecipient
from app.models.user import User
from app.services.newsletter_service import NewsletterService, PRICE_OF_DAY_SUBJECT
from app.services.notification_service import NotificationService
from app.config import settings

router = APIRouter()


class EmailPreferenceUpdate(BaseModel):
    marketing_opt_in: bool


class UnsubscribeRequest(BaseModel):
    token: str


def _state(pref: EmailPreference) -> dict:
    return {
        "email": pref.email,
        "marketing_opt_in": bool(pref.marketing_opt_in),
        "opted_in_at": pref.opted_in_at,
        "opted_out_at": pref.opted_out_at,
    }


async def _stop_pending_sends(db: AsyncSession, email: str) -> int:
    """Drop this address from every queued campaign.

    A campaign freezes its audience when it is queued, so an opt-out that arrives
    afterwards would otherwise still be mailed. Marking the pending rows skipped
    makes the unsubscribe immediate (the worker also re-checks consent per batch).
    """
    address = (email or "").strip().lower()
    if not address:
        return 0
    result = await db.execute(
        update(NewsletterRecipient)
        .where(
            func.lower(NewsletterRecipient.email) == address,
            NewsletterRecipient.status == "pending",
        )
        .values(status="skipped", error="unsubscribed")
    )
    return int(result.rowcount or 0)


async def _get_or_create(db: AsyncSession, user: User) -> EmailPreference:
    row = (await db.execute(select(EmailPreference).where(EmailPreference.user_id == user.id))).scalar_one_or_none()
    if row:
        return row
    row = EmailPreference(
        user_id=user.id,
        email=user.email or "",
        marketing_opt_in=False,
        unsubscribe_token=_uuid.uuid4().hex,
    )
    db.add(row)
    await db.flush()
    return row


@router.get("/preferences")
async def get_email_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's marketing opt-in state."""
    row = await _get_or_create(db, current_user)
    await db.commit()
    await db.refresh(row)
    return _state(row)


@router.post("/preferences")
async def set_email_preferences(
    payload: EmailPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set (opt in) or withdraw (opt out) marketing consent for the user."""
    now = datetime.utcnow()
    row = await _get_or_create(db, current_user)
    row.email = (current_user.email or row.email) or ""
    if payload.marketing_opt_in:
        row.marketing_opt_in = True
        row.opted_in_at = row.opted_in_at or now
        row.opted_out_at = None
        if not row.unsubscribe_token:
            row.unsubscribe_token = _uuid.uuid4().hex
    else:
        row.marketing_opt_in = False
        row.opted_out_at = now
        await _stop_pending_sends(db, row.email or (current_user.email or ""))
    row.updated_at = now
    await db.commit()
    await db.refresh(row)
    return _state(row)


@router.post("/broadcast/price-of-day")
async def broadcast_price_of_day(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 6,
):
    """Admin-triggered send of the market "price range of the day" newsletter.

    Only emails the addresses with `marketing_opt_in = True`. Sends are scheduled
    as background tasks (reusing NotificationService/Brevo) so the request returns
    immediately; this guard is admin/super_admin only (email sends cost money).
    """
    role = (current_user.role.value if getattr(current_user, "role", None) and hasattr(current_user.role, "value")
            else getattr(current_user, "role", None))
    if role not in ("admin", "super_admin"):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail="Admins only")

    service = NewsletterService()
    recipients = await service.opted_in_recipients(db)
    items = await service.build_price_of_day(db, limit=max(1, min(limit, 20)))
    base_url = settings.FRONTEND_URL
    html = service.build_html(items, base_url)

    if not recipients:
        return {"ok": True, "scheduled": 0, "recipient_count": 0, "materials": len(items),
                "message": "No opted-in recipients yet. Users must opt in before any marketing email."}

    notifier = NotificationService()
    for email in recipients:
        background_tasks.add_task(notifier.send_email, to=email, subject=PRICE_OF_DAY_SUBJECT, html_content=html)

    return {"ok": True, "scheduled": len(recipients), "recipient_count": len(recipients),
            "materials": len(items), "subject": PRICE_OF_DAY_SUBJECT}


@router.post("/unsubscribe")
async def unsubscribe_by_token(
    payload: UnsubscribeRequest,
    db: AsyncSession = Depends(get_db),
):
    """One-click unsubscribe by token (no login required)."""
    if not payload.token or not payload.token.strip():
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail="A token is required")
    row = (await db.execute(
        select(EmailPreference).where(EmailPreference.unsubscribe_token == payload.token.strip())
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Invalid unsubscribe link")
    row.marketing_opt_in = False
    row.opted_out_at = datetime.utcnow()
    row.updated_at = datetime.utcnow()
    await _stop_pending_sends(db, row.email)
    await db.commit()
    return {"ok": True, "message": "You have been unsubscribed from marketing emails."}
