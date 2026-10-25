"""Newsletter background task (Phase 3) - daily market "price range of the day".

Runs on the Celery `emails` queue (and beat schedule). Respects opt-in: only
`marketing_opt_in = True` addresses are emailed, composed from real
`material_rates` ranges, and sent through the same NotificationService/Brevo path
used for transactional email. NotificationService is async, so we drive it with
its own event loop here.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select

from app.core.celery_app import celery_app
from app.core.database import AsyncSessionLocal
from app.models.email_preference import EmailPreference
from app.models.newsletter_campaign import NewsletterCampaign
from app.services import newsletter_campaign_service as campaigns
from app.services.newsletter_render import render_email
from app.services.newsletter_service import NewsletterService, PRICE_OF_DAY_SUBJECT
from app.services.notification_service import NotificationService
from app.config import settings

logger = logging.getLogger(__name__)


async def _send_price_of_day(limit: int = 6):
    service = NewsletterService()
    async with AsyncSessionLocal() as db:
        recipients = await service.opted_in_recipients(db)
        items = await service.build_price_of_day(db, limit)
        html = service.build_html(items, settings.FRONTEND_URL or "")

    if not recipients:
        logger.info("newsletter price_of_day: no opted-in recipients; nothing sent")
        return {"sent": 0, "total": 0, "materials": len(items)}

    notifier = NotificationService()
    sent = 0
    for email in recipients:
        try:
            ok = await notifier.send_email(to=email, subject=PRICE_OF_DAY_SUBJECT, html_content=html)
            sent += 1 if ok else 0
        except Exception as exc:  # one bad recipient must not kill the batch
            logger.warning("newsletter price_of_day failed for %s: %s", email, exc)

    logger.info("newsletter price_of_day sent %s/%s", sent, len(recipients))
    return {"sent": sent, "total": len(recipients), "materials": len(items)}


@celery_app.task(name="send_price_of_day_newsletter")
def send_price_of_day_task(limit: int = 6):
    """Celery entry point; runs the async send on its own loop."""
    return asyncio.run(_send_price_of_day(limit))


# ── Campaign sender (admin console campaigns) ─────────────────────────────────
#
# Two tasks, deliberately split:
#   * `send_due_newsletter_campaigns` is the clock. It freezes each due campaign's
#     audience into `newsletter_recipients` (one short transaction per campaign)
#     and hands the id to the sender.
#   * `send_newsletter_campaign` is the worker. It claims a batch atomically
#     (`FOR UPDATE SKIP LOCKED`), renders per recipient, sends, then records the
#     outcome and commits - so a crash re-drives only rows still `pending`, and
#     nobody is mailed twice.


async def _opted_out_emails(db, emails: List[str]) -> set:
    """Addresses that unsubscribed after the audience was frozen.

    `queue_campaign` honours consent at queue time; this closes the window
    between queueing and the actual send, without re-resolving the segment.
    """
    if not emails:
        return set()
    rows = (await db.execute(
        select(EmailPreference.email).where(
            func.lower(EmailPreference.email).in_(emails),
            EmailPreference.opted_out_at.isnot(None),
            EmailPreference.marketing_opt_in.is_(False),
        )
    )).scalars().all()
    return {(row or "").strip().lower() for row in rows}


async def _send_campaign(campaign_id: str, limit: Optional[int] = None) -> Dict[str, Any]:
    """Send (or dry-run) one queued campaign to completion. Never raises."""
    cid = UUID(str(campaign_id))
    sent = retried = failed = skipped = 0

    async with AsyncSessionLocal() as db:
        campaign = await db.get(NewsletterCampaign, cid)
        if campaign is None:
            return {"campaign_id": str(cid), "skipped": True, "reason": "campaign not found"}
        if campaign.status not in {"queued", "sending"}:
            return {
                "campaign_id": str(cid),
                "skipped": True,
                "reason": f"campaign is '{campaign.status}'",
            }

        # Snapshot the content: the ORM row expires on every commit below, and a
        # lazy refresh mid-send would raise in async context.
        content = {
            "subject": campaign.subject,
            "body_html": campaign.body_html,
            "body_text": campaign.body_text,
        }
        dry_run = bool(campaign.dry_run)
        price_items = await campaigns.price_of_day_items(db)
        base_url = campaigns.base_url()

        campaign = await campaigns.mark_sending(db, campaign)
        await db.commit()

        notifier = NotificationService()
        gate = asyncio.Semaphore(max(1, int(settings.NEWSLETTER_SEND_CONCURRENCY)))

        async def _deliver(row: Dict[str, Any]) -> bool:
            message = render_email(content, row, base_url=base_url, price_items=price_items)
            async with gate:
                return bool(await notifier.send_email(
                    to=row["email"], subject=message.subject, html_content=message.html
                ))

        while True:
            await db.refresh(campaign)
            if campaign.status == "cancelled":
                logger.info("newsletter campaign %s cancelled mid-send", cid)
                break

            batch = await campaigns.claim_batch(db, campaign.id, limit)
            await db.commit()  # 'sending' must survive a crash before the network call
            if not batch:
                break

            # A cancel that landed while this batch was being claimed wins: the
            # rows are already 'sending', so release them instead of mailing them.
            await db.refresh(campaign)
            if campaign.status == "cancelled":
                for row in batch:
                    await campaigns.mark_skipped(db, row["id"], "campaign cancelled")
                    skipped += 1
                await db.commit()
                logger.info(
                    "newsletter campaign %s cancelled mid-batch; released %s row(s)",
                    cid, len(batch),
                )
                break

            # Opt-outs that arrived after queueing win over the frozen audience.
            opted_out = await _opted_out_emails(db, [str(row["email"]).strip().lower() for row in batch])
            blocked = {
                row["id"] for row in batch
                if str(row["email"]).strip().lower() in opted_out
            }
            deliverable = [row for row in batch if row["id"] not in blocked]
            for row in batch:
                if row["id"] in blocked:
                    await campaigns.mark_skipped(db, row["id"], "unsubscribed before send")
                    skipped += 1

            if dry_run:
                for row in deliverable:
                    await campaigns.mark_skipped(db, row["id"], "dry run - not sent")
                    skipped += 1
            else:
                results = await asyncio.gather(
                    *[_deliver(row) for row in deliverable], return_exceptions=True
                )
                for row, outcome in zip(deliverable, results):
                    if outcome is True:
                        await campaigns.record_sent(db, row["id"])
                        sent += 1
                        continue
                    if isinstance(outcome, BaseException):
                        reason = f"{type(outcome).__name__}: {outcome}"
                    else:
                        reason = "provider rejected the message"
                    status = await campaigns.record_failure(
                        db, row["id"], reason, attempts=int(row["attempts"] or 1)
                    )
                    if status == "failed":
                        failed += 1
                    else:
                        retried += 1
            await db.commit()

        await db.refresh(campaign)
        counts = await campaigns.finalize_campaign(db, campaign)
        final_status = campaign.status
        await db.commit()

    logger.info(
        "newsletter campaign %s -> %s (sent=%s failed=%s retried=%s skipped=%s)",
        cid, final_status, sent, failed, retried, skipped,
    )
    return {
        "campaign_id": str(cid),
        "status": final_status,
        "dry_run": dry_run,
        "sent": sent,
        "failed": failed,
        "retried": retried,
        "skipped": skipped,
        "counts": counts,
    }



async def _dispatch_due(limit: int = 20) -> Dict[str, Any]:
    """Queue every campaign whose time has come, then hand each to the sender.

    Also re-drives campaigns left `queued`/`sending` by a dead worker: an
    abandoned campaign would otherwise never be closed.
    """
    queued: List[str] = []
    failed: List[str] = []

    async with AsyncSessionLocal() as db:
        for cid in await campaigns.due_campaign_ids(db, limit=max(1, int(limit))):
            campaign = await db.get(NewsletterCampaign, cid)
            if campaign is None:
                continue
            try:
                await campaigns.queue_campaign(db, campaign)
            except campaigns.EmptyAudience as exc:
                campaign.status = "failed"
                campaign.last_error = str(exc)[:500]
                campaign.finished_at = datetime.utcnow()
                await db.commit()
                failed.append(str(cid))
                continue
            except campaigns.NewsletterError as exc:
                campaign.last_error = str(exc)[:500]
                await db.commit()
                failed.append(str(cid))
                continue
            await db.commit()
            queued.append(str(cid))

        cutoff = datetime.utcnow() - timedelta(
            minutes=max(1, int(settings.NEWSLETTER_STALE_MINUTES))
        )
        stale = (await db.execute(
            select(NewsletterCampaign.id)
            .where(
                NewsletterCampaign.status.in_(("queued", "sending")),
                NewsletterCampaign.updated_at < cutoff,
            )
            .limit(max(1, int(limit)))
        )).scalars().all()

    resumed = [str(cid) for cid in stale if str(cid) not in queued]
    for cid in queued + resumed:
        send_newsletter_campaign_task.delay(cid)

    if queued or resumed or failed:
        logger.info(
            "newsletter dispatch: queued=%s resumed=%s failed=%s",
            len(queued), len(resumed), len(failed),
        )
    return {"queued": queued, "resumed": resumed, "failed": failed}


@celery_app.task(name="send_newsletter_campaign")
def send_newsletter_campaign_task(campaign_id: str, limit: Optional[int] = None):
    """Celery entry point for one campaign; failures are returned, not raised."""
    try:
        return asyncio.run(_send_campaign(campaign_id, limit))
    except Exception as exc:  # a bad campaign must not poison the worker
        logger.exception("newsletter campaign %s failed: %s", campaign_id, exc)
        return {"campaign_id": str(campaign_id), "error": str(exc)}


@celery_app.task(name="send_due_newsletter_campaigns")
def send_due_newsletter_campaigns_task(limit: int = 20):
    """Beat tick: queue due campaigns and resume abandoned ones."""
    try:
        return asyncio.run(_dispatch_due(limit))
    except Exception as exc:
        logger.exception("newsletter dispatch failed: %s", exc)
        return {"error": str(exc)}

    return asyncio.run(_send_price_of_day(limit))
