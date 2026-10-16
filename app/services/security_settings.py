"""Read + enforce the Admin → Settings → Security switches.

The admin panel has always persisted these rows, but nothing consulted them, so
flipping a toggle did nothing. This module is the single reader: every switch is
resolved through :mod:`app.services.runtime_settings` (DB override → config
default), so an admin change takes effect on the next request.

Nothing here raises when the switch is OFF - the defaults are chosen so that
behaviour is unchanged until an admin opts in (with `password_attempts` staying
ON, since the lockout it governs is already always-on today).
"""
from __future__ import annotations

import ipaddress
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, Request, status

from app.services import runtime_settings

logger = logging.getLogger(__name__)

#: Roles that use the admin console (mirrors ADMIN_INBOX_ROLES / require_roles).
ADMIN_ROLES: frozenset[str] = frozenset(
    {"admin", "super_admin", "manager", "support", "marketing"}
)

#: Escape hatch so ops can always reach the console from a break-glass host.
IP_ALLOWLIST_ENV = "ADMIN_IP_ALLOWLIST_BYPASS"


# ── password policy ────────────────────────────────────────────────────────

def password_complexity_enabled() -> bool:
    return runtime_settings.get_bool("password_complexity", False)


def password_expiration_enabled() -> bool:
    return runtime_settings.get_bool("password_expiration", False)


def password_expiration_days() -> int:
    return max(1, runtime_settings.get_int("password_expiration_days", 90))


def password_policy_view() -> dict:
    """Shape returned by ``GET /auth/password-policy`` (drives the Admin UI hints)."""
    from app.utils.validators import BASELINE_MIN_LENGTH, STRICT_MIN_LENGTH

    strict = password_complexity_enabled()
    return {
        "complexity_required": strict,
        "min_length": STRICT_MIN_LENGTH if strict else BASELINE_MIN_LENGTH,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_digit": True,
        "require_special": strict,
        "expiration_enabled": password_expiration_enabled(),
        "expiration_days": password_expiration_days() if password_expiration_enabled() else None,
    }


def enforce_password_policy(password: str) -> None:
    """Raise 400 when `password_complexity` is ON and `password` breaks the policy."""
    if not password_complexity_enabled():
        return
    from app.utils.validators import password_policy_errors

    errors = password_policy_errors(password, strict=True)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Password does not meet the security policy.", "errors": errors},
        )


def is_password_expired(user) -> bool:
    """True when `password_expiration` is ON and the password is older than the limit."""
    if user is None or not password_expiration_enabled():
        return False
    changed_at = getattr(user, "password_changed_at", None)
    if changed_at is None:
        # Never recorded (pre-migration account): fall back to account creation so
        # a long-dormant account still ages out instead of being exempt forever.
        changed_at = getattr(user, "created_at", None)
    if changed_at is None:
        return False
    return datetime.utcnow() > changed_at + timedelta(days=password_expiration_days())


# ── login attempt lockout ──────────────────────────────────────────────────

def login_attempt_limit_enabled() -> bool:
    return runtime_settings.get_bool("password_attempts", True)


def login_attempt_threshold() -> int:
    return max(1, runtime_settings.get_int("password_attempts_threshold", 5))


def login_attempt_window_minutes() -> int:
    return max(1, runtime_settings.get_int("password_attempts_window_minutes", 15))


# ── session idle timeout ───────────────────────────────────────────────────

def session_timeout_enabled() -> bool:
    return runtime_settings.get_bool("security_session", False)


def session_idle_minutes() -> int:
    return max(1, runtime_settings.get_int("security_session_idle_minutes", 30))


def _redis():
    """Shared Redis client, or None when Redis is unreachable (callers fail open)."""
    try:
        import redis.asyncio as airedis
        from app.config import settings

        if getattr(_redis, "_client", None) is None:
            if settings.DEBUG:
                _redis._client = airedis.Redis(
                    host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                    db=settings.REDIS_DB, decode_responses=True,
                    socket_connect_timeout=2, socket_timeout=3,
                )
            else:
                _redis._client = airedis.from_url(
                    settings.REDIS_URL, ssl_cert_reqs=None, decode_responses=True,
                    socket_connect_timeout=2, socket_timeout=3,
                )
        return _redis._client
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("security_settings redis unavailable: %s", exc)
        return None


async def touch_session(user_id: str) -> None:
    """Record 'last seen now' for an admin session (best-effort)."""
    client = _redis()
    if client is None:
        return
    try:
        from app.config import settings as _s

        ttl = max(3600, _s.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
        await client.set(f"admin_last_seen:{user_id}", str(int(time.time())), ex=ttl)
    except Exception as exc:
        logger.debug("session touch failed: %s", exc)


async def enforce_session_idle(user_id: str) -> None:
    """Raise 401 when the admin has been idle beyond the configured limit.

    Fails **open** on a Redis outage (the session was still validly signed in);
    fails **closed** on a stale timestamp, which is the actual security control.
    """
    if not session_timeout_enabled():
        return
    client = _redis()
    if client is None:
        return
    try:
        raw = await client.get(f"admin_last_seen:{user_id}")
    except Exception as exc:
        logger.debug("session idle check skipped: %s", exc)
        return

    if raw is None:
        # No record (fresh session or the key aged out) - start the clock.
        await touch_session(user_id)
        return

    try:
        last_seen = int(float(raw))
    except (TypeError, ValueError):
        await touch_session(user_id)
        return

    if (time.time() - last_seen) > session_idle_minutes() * 60:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired due to inactivity",
            headers={"X-Session-Expired": "idle_timeout"},
        )
    await touch_session(user_id)


# ── IP allowlist ───────────────────────────────────────────────────────────

def ip_allowlist_enabled() -> bool:
    return runtime_settings.get_bool("security_ip", False)


def ip_allowlist() -> list[str]:
    return runtime_settings.get_list("security_ip_allowlist")


def client_ip(request: Request) -> str:
    """Client address, preferring the first hop in ``X-Forwarded-For`` when set."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def ip_allowed(request: Request) -> bool:
    """True when the request may reach the admin console.

    With the switch OFF every address is allowed. An empty allowlist is treated
    as 'nothing configured' and also allows through (the settings API refuses to
    enable the switch with an empty list, so this is a belt-and-braces default).
    """
    if not ip_allowlist_enabled():
        return True

    import os

    bypass = os.getenv(IP_ALLOWLIST_ENV, "")
    allowed = ip_allowlist()
    if not allowed:
        return True

    try:
        candidate = ipaddress.ip_address(client_ip(request))
    except ValueError:
        return False

    for entry in allowed + [b.strip() for b in bypass.split(",") if b.strip()]:
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if candidate in net:
            return True
    return False


# ── two-factor authentication ──────────────────────────────────────────────

"""Public alias - the 2FA service shares the same pooled connection."""
get_redis = _redis


def two_factor_enabled() -> bool:
    return runtime_settings.get_bool("security_2fa", False)


def two_factor_default_method() -> str:
    method = (runtime_settings.get_str("security_2fa_default_method", "email") or "email").strip().lower()
    return method if method in ("email", "totp") else "email"


def two_factor_remember_hours() -> int:
    return max(1, runtime_settings.get_int("security_2fa_remember_hours", 48))


def role_requires_2fa(role) -> bool:
    """Admin-tier roles must carry a second factor while `security_2fa` is ON."""
    role_value = getattr(role, "value", role)
    return two_factor_enabled() and str(role_value) in ADMIN_ROLES


def two_factor_status(user) -> dict:
    """Enrolment state for `user`, for the Admin Security card and admins list."""
    method = getattr(user, "two_factor_method", None)
    return {
        "required": role_requires_2fa(getattr(user, "role", None)),
        "enrolled": bool(method),
        "method": method,
        "enabled_at": (user.two_factor_enabled_at.isoformat()
                       if getattr(user, "two_factor_enabled_at", None) else None),
        "default_method": two_factor_default_method(),
        "remember_hours": two_factor_remember_hours(),
    }


