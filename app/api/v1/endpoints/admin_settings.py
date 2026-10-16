"""Phase 6 admin settings endpoints — system settings key-value store.

Powered by the SystemSetting model.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from typing import Optional

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.system_setting import SystemSetting
from app.services import runtime_settings

router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")

# ── Write allowlist ─────────────────────────────────────────────────────────
# Every backend-managed key (pricing/settlement/security/alerts) plus the handful
# of display-only keys the General/Integrations tabs have always written. Anything
# else is rejected so a typo can never create a phantom row that nothing reads.
DISPLAY_KEYS: tuple[str, ...] = (
    "platform_name",
    "support_email",
    "timezone",
    "language",
    "payment_provider",
    "api_key",
    "payment_test_mode",
    "integration_sms",
    "integration_email",
    "integration_analytics",
)
ALLOWED_KEYS: frozenset[str] = frozenset(runtime_settings.MANAGED_KEYS) | frozenset(DISPLAY_KEYS)

# Keys whose stored value must be a truthy/falsy flag string.
_BOOL_KEYS: frozenset[str] = frozenset(
    k for k, v in runtime_settings._defaults().items() if isinstance(v, bool)
)
_EMAIL_METHOD = "email"


def _to_bool(value: object) -> str:
    """Normalise any admin-supplied value to the canonical 'true'/'false'."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "true" if str(value).strip().lower() in ("1", "true", "yes", "on", "enabled") else "false"


def _email_provider_configured() -> bool:
    """True when an outbound email transport is actually configured."""
    from app.config import settings
    return bool(getattr(settings, "BREVO_API_KEY", None) or getattr(settings, "SMTP_HOST", None))


def _current_user_id(current_user) -> str:
    """Best-effort id for the acting admin (guard returns an ORM User, but stay
    tolerant of a mapping so this never breaks a settings write)."""
    uid = getattr(current_user, "id", None)
    if uid is None and isinstance(current_user, dict):
        uid = current_user.get("id")
    return str(uid or "admin")


@router.get("/settings")
async def admin_list_settings(section: Optional[str] = None, current_user=Depends(admin_guard), db=Depends(get_db)):
    """All system settings (optionally filtered by section)."""
    query = select(SystemSetting)
    if section:
        query = query.where(SystemSetting.section == section)
    rows = (await db.execute(query)).scalars().all()
    return {"settings": [{"key": s.key, "value": s.value, "section": s.section, "description": s.description} for s in rows]}


@router.put("/settings")
async def admin_update_settings(payload: dict, current_user=Depends(admin_guard), db=Depends(get_db)):
    """Bulk upsert system settings from a {key: value} map.

    Accepts an optional ``section`` (top level or inside the ``settings`` payload)
    so a page can scope the rows it writes; defaults to ``"general"`` for
    backwards compatibility. Existing rows keep their section unless one is given.

    Only :data:`ALLOWED_KEYS` may be written, and cross-field combinations that
    would lock everyone out are rejected (see the checks below).
    """
    values = payload.get("settings", payload)
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="Expected a {key: value} map")

    section = payload.get("section") if isinstance(payload, dict) else None
    if not isinstance(section, str) or not section:
        section = None

    # Only accept known keys; reject the whole request rather than silently
    # dropping a field the caller believed it had saved.
    unknown = sorted(k for k in values if isinstance(k, str) and k not in ALLOWED_KEYS and k not in ("section", "settings"))
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown setting(s): {', '.join(unknown)}")

    # `security_2fa=true` with the email method is only actionable when a mail
    # transport exists - otherwise no admin could ever complete enrolment.
    merged = dict(runtime_settings.snapshot())
    merged.update({k: _to_bool(v) if k in _BOOL_KEYS else str(v) for k, v in values.items()
                   if isinstance(k, str) and k in ALLOWED_KEYS})
    if merged.get("security_2fa") == "true":
        if merged.get("security_2fa_default_method") == _EMAIL_METHOD and not _email_provider_configured():
            raise HTTPException(
                status_code=400,
                detail="Email OTP is selected but no email provider (BREVO_API_KEY / SMTP_HOST) is configured. "
                       "Configure email or switch the default 2FA method to the authenticator app.",
            )
    # An IP allowlist that matches nothing would lock every admin out permanently.
    if merged.get("security_ip") == "true" and not runtime_settings.get_list("security_ip_allowlist"):
        raise HTTPException(
            status_code=400,
            detail="IP Whitelisting requires at least one address in the allowlist.",
        )

    updated_by = str(_current_user_id(current_user))
    now = datetime.utcnow()
    for key, value in values.items():
        if key in ("section", "settings") or not isinstance(key, str):
            continue
        stored = _to_bool(value) if key in _BOOL_KEYS else str(value)
        existing = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
        if existing:
            existing.value = stored
            if section:
                existing.section = section
            existing.updated_at = now
            existing.updated_by = updated_by
        else:
            db.add(SystemSetting(key=key, value=stored, section=section or "general", updated_by=updated_by))
    await db.commit()

    # Admin writes must be reflected immediately on hot paths (checkout, capture).
    from app.services import runtime_settings as _rs
    _rs.invalidate()
    await _rs.refresh(db, force=True)
    return {"updated": len(values), "section": section or "general"}