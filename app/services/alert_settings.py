"""Gate admin in-app alerts on the Admin → Settings → Notifications toggles.

The five switches on the Notifications tab map to notification *kinds*:

    alert_fraud     -> fraud          alert_escrow  -> escrow
    alert_supplier  -> verification   alert_dispute -> dispute
    alert_price     -> price

An admin row is only created (and only shown in the inbox) for a kind whose
toggle is ON. The underlying audit/fraud record is still written everywhere it
was before — the toggle silences the *inbox*, it never erases evidence.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.user import User, UserRole
from app.services import runtime_settings

logger = logging.getLogger(__name__)

ALERT_KEYS: dict[str, str] = {
    "fraud": "alert_fraud",
    "verification": "alert_supplier",
    "price": "alert_price",
    "escrow": "alert_escrow",
    "dispute": "alert_dispute",
}

#: Notification.type value -> alert kind. Types absent from this map (negotiation,
#: system, order…) are unaffected by these toggles and always remain visible.
TYPE_TO_KIND: dict[str, str] = {
    "fraud": "fraud",
    "alert": "fraud",
    "verification": "verification",
    "vendor_verification": "verification",
    "price": "price",
    "price_integrity": "price",
    "escrow": "escrow",
    "payment": "escrow",
    "dispute": "dispute",
}

ADMIN_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.MANAGER,
               UserRole.SUPPORT, UserRole.MARKETING)


def alert_enabled(kind: str) -> bool:
    """True when the toggle for `kind` is ON (unknown kinds are always allowed)."""
    key = ALERT_KEYS.get(kind)
    if key is None:
        return True
    return runtime_settings.get_bool(key, True)


def hidden_types() -> list[str]:
    """Notification types whose toggle is OFF, for the admin inbox filter."""
    return [type_name for type_name, kind in TYPE_TO_KIND.items() if not alert_enabled(kind)]


async def notify_admins(
    db: AsyncSession,
    *,
    kind: str,
    title: str,
    message: str,
    link: str | None = None,
    type_name: str | None = None,
    commit: bool = True,
) -> int:
    """Queue one notification per admin-tier user when `kind` is enabled.

    Returns the number of rows added. Callers that are already inside a
    transaction can pass ``commit=False`` and commit themselves.
    """
    if not alert_enabled(kind):
        return 0

    result = await db.execute(
        select(User).where(User.role.in_(ADMIN_ROLES))
    )
    admins = result.scalars().all()
    for admin in admins:
        db.add(Notification(
            user_id=admin.id,
            type=type_name or kind,
            title=title,
            message=message,
            read=False,
            link=link,
        ))
    if commit and admins:
        await db.commit()
    return len(admins)
