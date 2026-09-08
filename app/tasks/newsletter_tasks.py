"""Newsletter background task (Phase 3) — daily market "price range of the day".

Runs on the Celery `emails` queue (and beat schedule). Respects opt-in: only
`marketing_opt_in = True` addresses are emailed, composed from real
`material_rates` ranges, and sent through the same NotificationService/Brevo path
used for transactional email. NotificationService is async, so we drive it with
its own event loop here.
"""
import asyncio
import logging
import os

from app.core.celery_app import celery_app
from app.core.database import AsyncSessionLocal
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
