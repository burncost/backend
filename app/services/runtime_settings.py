"""DB-backed runtime overrides for platform pricing/settlement settings (Phase 0).

Admin-editable values live in the `system_settings` key-value table. Hot paths
(checkout, payment capture, settlement accrual) must not pay a DB round-trip per
call, so this module keeps a tiny process-local cache with a short TTL.

Every read falls back to the `app.config.settings` default when the key is absent
or the database is unreachable, so the app behaves identically to before whenever
nothing has been configured in the admin panel.

Usage:
    await runtime_settings.refresh(db)          # cheap; only hits the DB when stale
    runtime_settings.get_bool("vat_enabled", True)
    runtime_settings.get_decimal("vat_rate", Decimal("7.5"))
    runtime_settings.get_str("payout_primary_gateway", "paystack")
"""
from __future__ import annotations

import logging
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# The only keys this module reads/writes. Refreshing never touches anything else.
MANAGED_KEYS: tuple[str, ...] = (
    "settlement_enabled",
    "settlement_commission_rate",
    "payout_primary_gateway",
    "payout_backup_gateway",
    "client_margin_share",
    "vendor_margin_share",
    "vat_enabled",
    "vat_rate",
    "vat_base",
    # Security: password policy
    "password_complexity",
    "password_expiration",
    "password_expiration_days",
    "password_attempts",
    "password_attempts_threshold",
    "password_attempts_window_minutes",
    # Security: authentication
    "security_2fa",
    "security_2fa_default_method",
    "security_2fa_remember_hours",
    "security_session",
    "security_session_idle_minutes",
    "security_ip",
    "security_ip_allowlist",
    # Security: notification alerts
    "alert_fraud",
    "alert_supplier",
    "alert_price",
    "alert_escrow",
    "alert_dispute",
)

#: How long a cached snapshot stays valid before the next `refresh()` re-reads.
TTL_SECONDS: float = 30.0

_cache: dict[str, str] = {}
_loaded_at: float = 0.0


def _defaults() -> dict[str, object]:
    """Config-level fallbacks, resolved lazily so tests can monkeypatch settings."""
    return {
        "settlement_enabled": getattr(settings, "SETTLEMENT_ENABLED", False),
        "settlement_commission_rate": getattr(settings, "SETTLEMENT_COMMISSION_RATE", "0.0"),
        "payout_primary_gateway": getattr(settings, "PAYOUT_PRIMARY_GATEWAY", "paystack"),
        "payout_backup_gateway": getattr(settings, "PAYOUT_BACKUP_GATEWAY", "monnify"),
        "client_margin_share": getattr(settings, "CLIENT_MARGIN_SHARE", "0.40"),
        "vendor_margin_share": getattr(settings, "VENDOR_MARGIN_SHARE", "0.60"),
        "vat_enabled": getattr(settings, "VAT_ENABLED", True),
        "vat_rate": getattr(settings, "VAT_RATE", "7.5"),
        "vat_base": getattr(settings, "VAT_BASE", "subtotal_plus_margin"),
        # ── Admin security toggles (Admin → Settings → Security) ────────────
        # `password_attempts` defaults ON: the Redis lockout is already always-on
        # today, so defaulting it OFF would silently weaken login protection.
        "password_complexity": getattr(settings, "PASSWORD_COMPLEXITY", False),
        "password_expiration": getattr(settings, "PASSWORD_EXPIRATION", False),
        "password_expiration_days": getattr(settings, "PASSWORD_EXPIRATION_DAYS", "90"),
        "password_attempts": getattr(settings, "PASSWORD_ATTEMPTS", True),
        "password_attempts_threshold": getattr(settings, "PASSWORD_ATTEMPTS_THRESHOLD", "5"),
        "password_attempts_window_minutes": getattr(settings, "PASSWORD_ATTEMPTS_WINDOW_MINUTES", "15"),
        "security_2fa": getattr(settings, "SECURITY_2FA", False),
        "security_2fa_default_method": getattr(settings, "SECURITY_2FA_DEFAULT_METHOD", "email"),
        "security_2fa_remember_hours": getattr(settings, "SECURITY_2FA_REMEMBER_HOURS", "48"),
        "security_session": getattr(settings, "SECURITY_SESSION", False),
        "security_session_idle_minutes": getattr(settings, "SECURITY_SESSION_IDLE_MINUTES", "30"),
        "security_ip": getattr(settings, "SECURITY_IP", False),
        "security_ip_allowlist": getattr(settings, "SECURITY_IP_ALLOWLIST", ""),
        # ── Admin notification alerts ──────────────────────────────────────
        "alert_fraud": getattr(settings, "ALERT_FRAUD", True),
        "alert_supplier": getattr(settings, "ALERT_SUPPLIER", True),
        "alert_price": getattr(settings, "ALERT_PRICE", True),
        "alert_escrow": getattr(settings, "ALERT_ESCROW", True),
        "alert_dispute": getattr(settings, "ALERT_DISPUTE", True),
    }


def _raw(key: str) -> Optional[str]:
    """Cached override for `key`, or None when unset."""
    value = _cache.get(key)
    return value if value not in (None, "") else None


def _fresh() -> bool:
    return (time.time() - _loaded_at) < TTL_SECONDS


def invalidate() -> None:
    """Drop the cached snapshot (called after an admin write)."""
    global _loaded_at
    _loaded_at = 0.0


async def refresh(db: Optional[AsyncSession], *, force: bool = False) -> None:
    """Reload the managed keys from `system_settings` when the cache is stale.

    Best-effort: any failure leaves the previous snapshot in place, and reads
    simply fall back to config defaults. Never raises.
    """
    global _loaded_at
    if db is None:
        return
    if not force and _fresh():
        return
    try:
        from app.models.system_setting import SystemSetting

        rows = (await db.execute(
            select(SystemSetting.key, SystemSetting.value).where(
                SystemSetting.key.in_(MANAGED_KEYS)
            )
        )).all()
        loaded = {str(k): ("" if v is None else str(v)) for k, v in rows}
        _cache.update(loaded)
        _loaded_at = time.time()
    except Exception as exc:  # pragma: no cover - defensive, DB may be down
        logger.debug("runtime settings refresh skipped: %s", exc)
        _loaded_at = time.time()


# ── Typed accessors ─────────────────────────────────────────────────────────
# Resolution order: DB override -> config default -> explicit fallback.
# (The config default is consulted even when a fallback is passed, so changing
# `app.config.settings` is always honoured.)

def get_str(key: str, default: Optional[str] = None) -> Optional[str]:
    raw = _raw(key)
    if raw is not None:
        return raw
    cfg = _defaults().get(key)
    if cfg is not None:
        return str(cfg)
    return default


def get_bool(key: str, default: Optional[bool] = None) -> bool:
    raw = _raw(key)
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on", "enabled")
    cfg = _defaults().get(key)
    if cfg is not None:
        return bool(cfg)
    return bool(default)


def get_decimal(key: str, default: Optional[Decimal] = None) -> Decimal:
    raw = _raw(key)
    if raw is not None:
        try:
            return Decimal(raw.strip())
        except (InvalidOperation, ValueError):
            pass
    cfg = _defaults().get(key)
    if cfg is not None:
        try:
            return Decimal(str(cfg))
        except (InvalidOperation, ValueError, TypeError):
            pass
    return default if default is not None else Decimal("0")


def get_int(key: str, default: Optional[int] = None) -> int:
    """Integer override, e.g. `get_int("security_session_idle_minutes", 30)`."""
    raw = _raw(key)
    if raw is not None:
        try:
            return int(float(raw.strip()))
        except (TypeError, ValueError):
            pass
    cfg = _defaults().get(key)
    if cfg is not None:
        try:
            return int(float(str(cfg)))
        except (TypeError, ValueError):
            pass
    return int(default) if default is not None else 0


def get_list(key: str) -> list[str]:
    """Comma/newline separated override split into a trimmed, non-empty list."""
    raw = _raw(key)
    if raw is None:
        cfg = _defaults().get(key)
        raw = "" if cfg is None else str(cfg)
    return [part.strip() for part in re.split(r"[,\n;]+", raw or "") if part.strip()]


def snapshot() -> dict[str, str]:
    """Effective values (override when set, else config default) for all managed keys."""
    out: dict[str, str] = {}
    for key in MANAGED_KEYS:
        raw = _raw(key)
        if raw is not None:
            out[key] = raw
        else:
            value = _defaults().get(key)
            if isinstance(value, bool):
                out[key] = "true" if value else "false"
            else:
                out[key] = "" if value is None else str(value)
    return out
