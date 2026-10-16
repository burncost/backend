"""Promo (discount code) delivery — fans a new promo out to the users it reaches.

Promos used to be discoverable only by typing the code into the cart. This
service surfaces them through the notification channels the app already has:

* **In-app** — one `notifications` row per eligible user (`type="promotion"`,
  already rendered by the customer/supplier notification screens). Delivered to
  everyone who has not explicitly opted out of promo alerts (`push_promotions`
  for customers, `promotions` for vendors; an absent key means "not opted out").
* **Email** — only to users who explicitly opted in to marketing email, via the
  granular `email_promotions` toggle or the `user_email_preferences` consent row
  (`marketing_opt_in`).

Audience follows the promo scope:

* admin `platform`/`category` -> every active customer & vendor.
* vendor `vendor`/`product`/`category` -> the owning vendor's past customers
  (users with an order line from that vendor).

Everything here is best-effort: callers wrap `notify_promo_created` so a
marketing hiccup can never block promo creation.
"""
from typing import List, Optional, Tuple
from uuid import UUID
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTasks

from app.core.database import AsyncSessionLocal
from app.models.email_preference import EmailPreference
from app.models.notification import Notification
from app.models.order import Order, OrderItem
from app.models.promo import PromoCode
from app.models.user import User, UserProfile, UserRole, UserStatus
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# Safety valve so a single broadcast can never queue an unbounded amount of
# email; in-app rows are cheap and are still written for every eligible user.
MAX_EMAIL_RECIPIENTS = 500


def _in_app_enabled(prefs: Optional[dict]) -> bool:
    """In-app promo alert is on unless the user explicitly switched it off.

    Customers persist `push_promotions`, vendors persist `promotions` (see
    Settings.tsx / SupplierSettings.tsx). An absent key means not opted out.
    """
    prefs = prefs or {}
    return prefs.get("push_promotions", True) is not False and prefs.get("promotions", True) is not False


def _email_enabled(prefs: Optional[dict], email: Optional[str], opted_in_emails: set) -> bool:
    """Email requires an explicit opt-in — granular toggle or consent record."""
    prefs = prefs or {}
    if prefs.get("email_promotions") is True:
        return True
    return (email or "").strip().lower() in opted_in_emails


def _recipient_link(role) -> str:
    """Where a promo notification should open for a given recipient role.

    Promo rows used to carry a single "/dashboard" link for the whole audience,
    which dropped vendors on the builder dashboard. Vendors redeem codes in their
    Supplier Dashboard's Promotions tab. `role` may arrive as a UserRole member or
    a plain string depending on the driver.
    """
    code = str(getattr(role, "value", role) or "").strip().lower()
    if code == "vendor":
        return "/supplier-dashboard?tab=promotions"
    return "/dashboard"


def _compose(promo, scope_label: Optional[str]) -> Tuple[str, str, str]:
    """(title, message, link) shared by the in-app row and the email."""
    amount = float(promo.discount_amount or 0)
    target = f" {scope_label}" if scope_label else " your order"
    message = f"Use code {promo.code} for ₦{amount:,.0f} off{target}."
    min_order = float(promo.min_order_amount or 0)
    if min_order > 0:
        message += f" Minimum order ₦{min_order:,.0f}."
    if promo.expires_at:
        try:
            message += f" Expires {promo.expires_at.strftime('%d %b %Y')}."
        except Exception:
            pass
    # Promo codes are redeemed in the cart; the dashboard is the safe in-app
    # landing. This is the builder baseline only - the in-app row overrides it per
    # recipient via `_recipient_link` so vendors stay on their own dashboard.
    return f"New promo: {promo.code}", message, "/dashboard"


async def _recipient_rows(db: AsyncSession, vendor_id):
    """(user_id, email, role, notification_preferences) for everyone the promo reaches."""
    stmt = (
        select(User.id, User.email, User.role, UserProfile.notification_preferences)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
    )
    if vendor_id is not None:
        customer_ids = (
            select(Order.user_id)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(OrderItem.vendor_id == vendor_id)
            .distinct()
        )
        stmt = stmt.where(User.id.in_(customer_ids))
    else:
        stmt = stmt.where(
            User.status == UserStatus.ACTIVE,
            User.role.in_((UserRole.CUSTOMER, UserRole.VENDOR)),
        )
    return (await db.execute(stmt)).all()


async def _opted_in_emails(db: AsyncSession) -> set:
    rows = (await db.execute(
        select(EmailPreference.email).where(
            EmailPreference.marketing_opt_in.is_(True),
            EmailPreference.email.isnot(None),
        )
    )).scalars().all()
    return {(e or "").strip().lower() for e in rows if e and e.strip()}


async def send_promo_emails(
    recipients: List[str],
    code: str,
    discount_amount: float,
    description: Optional[str] = None,
    min_order_amount: float = 0,
    expires_at=None,
    scope_label: Optional[str] = None,
) -> int:
    """Background fan-out of the promo email (one bad address must not kill the batch)."""
    notifier = NotificationService()
    sent = 0
    for email in recipients:
        try:
            ok = await notifier.send_promo_email(
                email=email, code=code, discount_amount=discount_amount,
                description=description, min_order_amount=min_order_amount,
                expires_at=expires_at, scope_label=scope_label,
            )
            sent += 1 if ok else 0
        except Exception as exc:
            logger.warning("promo %s email failed for %s: %s", code, email, exc)
    logger.info("promo %s emails sent %s/%s", code, sent, len(recipients))
    return sent


async def notify_promo_created(
    db: AsyncSession,
    promo,
    *,
    vendor_id=None,
    scope_label: Optional[str] = None,
    background_tasks=None,
) -> dict:
    """Deliver a freshly created promo: in-app rows now + opted-in emails in the background.

    Returns {"in_app": n, "email": n}. Never raises — delivery is best-effort.
    """
    t_lookup = time.monotonic()
    try:
        rows = await _recipient_rows(db, vendor_id)
        opted_in = await _opted_in_emails(db)
    except Exception as exc:
        logger.error("promo notifications: audience lookup failed for %s: %s", promo.code, exc)
        return {"in_app": 0, "email": 0}
    logger.info(
        "promo %s audience lookup: %s row(s) in %.2fs",
        promo.code, len(rows), time.monotonic() - t_lookup,
    )

    if not rows:
        return {"in_app": 0, "email": 0}

    title, message, _ = _compose(promo, scope_label)

    in_app = 0
    email_recipients: List[str] = []
    for user_id, email, role, prefs in rows:
        if _in_app_enabled(prefs):
            db.add(Notification(user_id=user_id, type="promotion", title=title,
                                message=message, read=False,
                                link=_recipient_link(role)))
            in_app += 1
        if _email_enabled(prefs, email, opted_in):
            email_recipients.append(email)

    if in_app:
        t_commit = time.monotonic()
        try:
            await db.commit()
            logger.info(
                "promo %s in-app commit: %s row(s) in %.2fs",
                promo.code, in_app, time.monotonic() - t_commit,
            )
        except Exception as exc:
            await db.rollback()
            logger.error("promo notifications: in-app insert failed for %s: %s", promo.code, exc)
            in_app = 0

    if background_tasks is not None and email_recipients:
        background_tasks.add_task(
            send_promo_emails,
            email_recipients[:MAX_EMAIL_RECIPIENTS],
            promo.code,
            float(promo.discount_amount or 0),
            promo.description,
            float(promo.min_order_amount or 0),
            promo.expires_at,
            scope_label,
        )

    logger.info("promo %s delivered: %s in-app, %s email(s)", promo.code, in_app, len(email_recipients))
    return {"in_app": in_app, "email": len(email_recipients)}


async def deliver_promo_created(
    promo_id: UUID,
    *,
    vendor_id=None,
    scope_label: Optional[str] = None,
) -> None:
    """Fan a new promo out from a background task, off the request path.

    `notify_promo_created` is slow for a platform promo (it addresses every active
    user), so the endpoints queue this instead of awaiting it. It opens its **own**
    session: the request's `db` (from `get_db`) is closed by the time Starlette runs
    background tasks, so reusing it would fail on a closed session.

    Never raises — the promo itself is already committed by the caller.
    """
    started = time.monotonic()
    try:
        async with AsyncSessionLocal() as db:
            promo = await db.get(PromoCode, promo_id)
            if promo is None:
                logger.warning("promo %s vanished before delivery", promo_id)
                return
            # Builds the in-app rows (committed here) and queues the opted-in
            # emails onto `bg`; draining `bg` right after still sends them.
            bg = BackgroundTasks()
            await notify_promo_created(
                db, promo, vendor_id=vendor_id, scope_label=scope_label, background_tasks=bg,
            )
            await bg()
    except Exception as exc:
        logger.error("promo %s background delivery failed: %s", promo_id, exc)
        return
    logger.info("promo %s background delivery finished in %.2fs", promo_id, time.monotonic() - started)
