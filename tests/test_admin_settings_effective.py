"""The Admin → Settings → Security/Notifications switches must actually do something.

Before this work all eleven toggles persisted to `system_settings` and were then
ignored by every code path, so flipping one changed nothing. These tests pin the
behaviour each switch now controls, so a future refactor cannot quietly turn them
back into decoration.

Everything is DB-less: the runtime_settings cache is overridden in place and the
handful of collaborators are stubbed, matching `test_auth_refresh_resilience.py`.
"""

import asyncio
import json
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.v1.endpoints import auth
from app.api import deps
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.services import alert_settings, runtime_settings, security_settings, two_factor
from app.schemas.user import UserLogin
from app.utils.validators import password_policy_errors

USER_ID = "11111111-1111-1111-1111-111111111111"


# ── helpers ────────────────────────────────────────────────────────────────

@contextmanager
def settings(**overrides):
    """Temporarily override runtime settings (DB override -> config -> fallback)."""
    snapshot = dict(runtime_settings._cache)
    runtime_settings._cache.update({k: str(v) for k, v in overrides.items()})
    try:
        yield
    finally:
        runtime_settings._cache.clear()
        runtime_settings._cache.update(snapshot)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Every test starts from (and leaves) a clean runtime-settings cache."""
    snapshot = dict(runtime_settings._cache)
    runtime_settings._cache.clear()
    yield
    runtime_settings._cache.clear()
    runtime_settings._cache.update(snapshot)


class StubRedis:
    """In-memory stand-in for the commands the 2FA/session helpers use."""

    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.sets = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.sets[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key) or 0) + 1)
        return int(self.store[key])

    async def expire(self, key, ttl):
        return True

    async def sadd(self, key, member):
        self.store.setdefault(key, set()).add(member)
        return 1

    async def srem(self, key, member):
        bucket = self.store.get(key)
        if isinstance(bucket, set):
            bucket.discard(member)
        return 1

    async def smembers(self, key):
        return set(self.store.get(key) or set())


def _request(path="/api/v1/admin/stats", token=None, ip="203.0.113.7", xff=None, cookies=None) -> Request:
    headers = []
    if xff:
        headers.append((b"x-forwarded-for", xff.encode()))
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if cookies:
        headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": (ip, 50000),
        "server": ("testserver", 80),
    })


def _login_stubs(monkeypatch, user, redis=None):
    """Wire up the collaborators `auth.login` needs, without a database."""
    monkeypatch.setattr(auth, "redis_client", redis or StubRedis())
    monkeypatch.setattr(auth.user_crud, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(auth.user_crud, "update_last_login", AsyncMock(return_value=user))
    monkeypatch.setattr(auth.vendor_crud, "get_by_user_id", AsyncMock(return_value=None))
    monkeypatch.setattr(auth, "clear_failed_logins", AsyncMock())
    monkeypatch.setattr(auth, "record_failed_login", AsyncMock())


def _admin_user(**overrides):
    user = SimpleNamespace(
        id=USER_ID,
        email="admin@burncost.com",
        password_hash="$argon2id$stub",
        role=SimpleNamespace(value="admin"),
        status="active",
        profile=SimpleNamespace(first_name="Ada", business_name=None),
        two_factor_method=None,
        two_factor_enabled_at=None,
        totp_secret=None,
        totp_recovery_codes=None,
        password_changed_at=None,
        created_at=None,
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


# ── password policy ────────────────────────────────────────────────────────

def test_password_policy_errors_baseline_vs_strict():
    assert password_policy_errors("Short1", strict=False)           # too short
    assert password_policy_errors("alllowercase1", strict=False)    # no uppercase
    assert password_policy_errors("Password1", strict=False) == []  # baseline rule set
    # The strict set only bites once the toggle is on.
    assert password_policy_errors("Password1", strict=True)
    assert password_policy_errors("Password123!", strict=True) == []


def test_password_complexity_toggle_controls_enforcement():
    weak = "Password1"  # passes baseline, fails the 12+special rule

    with settings(password_complexity="false"):
        security_settings.enforce_password_policy(weak)  # no-op

    with settings(password_complexity="true"):
        with pytest.raises(HTTPException) as caught:
            security_settings.enforce_password_policy(weak)
        assert caught.value.status_code == 400
        # The client shows these verbatim, so they must be a list of reasons.
        assert caught.value.detail["errors"]


def test_password_policy_view_reflects_settings():
    with settings(password_complexity="false", password_expiration="false"):
        view = security_settings.password_policy_view()
    assert view["complexity_required"] is False
    assert view["min_length"] == 8
    assert view["expiration_days"] is None

    with settings(password_complexity="true", password_expiration="true", password_expiration_days="30"):
        view = security_settings.password_policy_view()
    assert view["min_length"] == 12
    assert view["require_special"] is True
    assert view["expiration_days"] == 30


def test_password_expiration_math_uses_configured_days():
    old = _admin_user(password_changed_at=None, created_at=None)
    # Never recorded and no created_at -> cannot be aged out (fail safe).
    with settings(password_expiration="true", password_expiration_days="90"):
        assert security_settings.is_password_expired(old) is False

    fresh = _admin_user(password_changed_at=None, created_at=None)
    fresh.password_changed_at = datetime.utcnow()
    with settings(password_expiration="true", password_expiration_days="90"):
        assert security_settings.is_password_expired(fresh) is False

    stale = _admin_user(password_changed_at=None, created_at=None)
    stale.password_changed_at = datetime.utcnow() - timedelta(days=91)
    with settings(password_expiration="true", password_expiration_days="90"):
        assert security_settings.is_password_expired(stale) is True
    # Toggle off -> never expired, regardless of age.
    with settings(password_expiration="false"):
        assert security_settings.is_password_expired(stale) is False


def test_login_attempt_limit_reads_configured_threshold():
    with settings(password_attempts="true", password_attempts_threshold="3",
                  password_attempts_window_minutes="7"):
        assert security_settings.login_attempt_limit_enabled() is True
        assert security_settings.login_attempt_threshold() == 3
        assert security_settings.login_attempt_window_minutes() == 7

    with settings(password_attempts="false"):
        assert security_settings.login_attempt_limit_enabled() is False


def test_lockout_defaults_to_on_so_protection_never_regresses():
    # No row configured at all: the Redis lockout that exists today must stay live.
    assert security_settings.login_attempt_limit_enabled() is True
    assert security_settings.login_attempt_threshold() == 5


# ── notification alert toggles ─────────────────────────────────────────────

def test_alert_toggles_gate_admin_notifications():
    with settings(alert_fraud="false", alert_dispute="false"):
        hidden = alert_settings.hidden_types()
        assert "fraud" in hidden and "alert" in hidden and "dispute" in hidden
        assert "verification" not in hidden          # still on -> still visible
        assert alert_settings.alert_enabled("fraud") is False
        assert alert_settings.alert_enabled("verification") is True

    # Defaults are ON, so enabling the policy changes nothing until an admin opts out.
    for kind in ("fraud", "verification", "price", "escrow", "dispute"):
        assert alert_settings.alert_enabled(kind) is True
    assert alert_settings.hidden_types() == []


def test_notify_admins_is_a_noop_when_the_kind_is_disabled():
    with settings(alert_supplier="false"):
        count = asyncio.run(alert_settings.notify_admins(
            object(), kind="verification", title="t", message="m",
        ))
    assert count == 0  # no DB round-trip attempted


# ── IP allowlist ───────────────────────────────────────────────────────────

def test_ip_allowlist_blocks_unlisted_and_allows_listed():
    user = _admin_user()
    with settings(security_ip="true", security_ip_allowlist="10.0.0.0/8"):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(deps.enforce_admin_access(request=_request(ip="203.0.113.7"), current_user=user))
        assert caught.value.detail["code"] == "ip_not_allowed"
        assert caught.value.status_code == 403

        assert asyncio.run(deps.enforce_admin_access(request=_request(ip="10.1.2.3"), current_user=user)) is user
        # First X-Forwarded-For hop wins, so a proxy in front does not defeat it.
        assert asyncio.run(deps.enforce_admin_access(
            request=_request(ip="172.16.0.1", xff="10.9.9.9, 172.16.0.1"), current_user=user)) is user

    # Switch off -> everything is allowed again.
    with settings(security_ip="false", security_ip_allowlist="10.0.0.0/8"):
        assert asyncio.run(deps.enforce_admin_access(request=_request(ip="203.0.113.7"), current_user=user)) is user


def test_ip_allowlist_empty_list_does_not_lock_everyone_out():
    user = _admin_user()
    with settings(security_ip="true", security_ip_allowlist=""):
        assert asyncio.run(deps.enforce_admin_access(request=_request(ip="203.0.113.7"), current_user=user)) is user


# ── session idle timeout ───────────────────────────────────────────────────

def _use_redis(monkeypatch, redis):
    """Point both Redis accessors used by the security helpers at a stub."""
    monkeypatch.setattr(security_settings, "_redis", lambda: redis)
    monkeypatch.setattr(security_settings, "get_redis", lambda: redis)


def test_session_idle_timeout_flags_stale_sessions_only(monkeypatch):
    stale = StubRedis({f"admin_last_seen:{USER_ID}": str(int(time.time()) - 10 * 60)})
    _use_redis(monkeypatch, stale)
    with settings(security_session="true", security_session_idle_minutes="5"):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(security_settings.enforce_session_idle(USER_ID))
    assert caught.value.status_code == 401
    assert caught.value.headers["X-Session-Expired"] == "idle_timeout"

    fresh = StubRedis({f"admin_last_seen:{USER_ID}": str(int(time.time()) - 30)})
    _use_redis(monkeypatch, fresh)
    with settings(security_session="true", security_session_idle_minutes="5"):
        asyncio.run(security_settings.enforce_session_idle(USER_ID))  # no raise
        # ...and the clock was pushed forward for the next request.
        assert int(fresh.store[f"admin_last_seen:{USER_ID}"]) >= int(time.time()) - 2


def test_session_idle_is_skipped_entirely_when_the_toggle_is_off():
    with settings(security_session="false"):
        asyncio.run(security_settings.enforce_session_idle(USER_ID))  # no Redis, no raise


# ── authenticator app + recovery codes ─────────────────────────────────────

def test_totp_verify_accepts_the_current_code_only():
    import pyotp

    secret = two_factor.generate_totp_secret()
    assert two_factor.verify_totp(secret, pyotp.TOTP(secret).now()) is True
    assert two_factor.verify_totp(secret, "000000") is False
    assert two_factor.verify_totp(None, "123456") is False
    assert two_factor.verify_totp(secret, "abcdef") is False


def test_totp_secret_is_encrypted_at_rest_and_round_trips():
    secret = two_factor.generate_totp_secret()
    stored = two_factor.encrypt_secret(secret)
    assert stored != secret
    assert secret not in stored                       # never stored in the clear
    assert two_factor.decrypt_secret(stored) == secret
    assert two_factor.decrypt_secret(None) is None


def test_qr_provisioning_uri_is_renderable_without_an_npm_dependency():
    secret = two_factor.generate_totp_secret()
    uri = two_factor.provisioning_uri(secret, "admin@burncost.com")
    assert uri.startswith("otpauth://totp/")
    assert "Burncost" in uri
    assert two_factor.qr_data_uri(uri).startswith("data:image/png;base64,")


def test_recovery_codes_are_hashed_and_single_use():
    codes = two_factor.generate_recovery_codes()
    assert len(codes) == 10
    stored = two_factor.hash_recovery_codes(codes)
    assert codes[0] not in stored                     # hashed, not plaintext

    ok, remaining = two_factor.verify_and_consume_recovery_code(stored, codes[0])
    assert ok is True
    # Replaying the same code must fail...
    again, _ = two_factor.verify_and_consume_recovery_code(remaining, codes[0])
    assert again is False
    # ...while the rest of the set is untouched.
    other, _ = two_factor.verify_and_consume_recovery_code(remaining, codes[1])
    assert other is True


# ── "remember this device" ─────────────────────────────────────────────────

def test_trust_token_skips_then_expires_and_can_be_revoked(monkeypatch):
    _use_redis(monkeypatch, StubRedis())
    token = asyncio.run(two_factor.create_trust(USER_ID, 48, method="totp"))
    assert token

    assert asyncio.run(two_factor.is_trusted(USER_ID, token)) is True
    # A token is bound to one account: it must not skip 2FA for anybody else.
    assert asyncio.run(two_factor.is_trusted("22222222-2222-2222-2222-222222222222", token)) is False

    devices = asyncio.run(two_factor.list_devices(USER_ID))
    assert len(devices) == 1
    assert devices[0]["method"] == "totp"

    asyncio.run(two_factor.revoke_device(USER_ID, devices[0]["id"]))
    assert asyncio.run(two_factor.is_trusted(USER_ID, token)) is False
    assert asyncio.run(two_factor.list_devices(USER_ID)) == []


def test_trust_fails_closed_when_redis_is_unavailable(monkeypatch):
    _use_redis(monkeypatch, None)
    assert asyncio.run(two_factor.is_trusted(USER_ID, "some-token")) is False


def test_revoke_all_clears_every_remembered_device(monkeypatch):
    _use_redis(monkeypatch, StubRedis())
    first = asyncio.run(two_factor.create_trust(USER_ID, 48))
    second = asyncio.run(two_factor.create_trust(USER_ID, 48))

    asyncio.run(two_factor.revoke_all(USER_ID))

    assert asyncio.run(two_factor.is_trusted(USER_ID, first)) is False
    assert asyncio.run(two_factor.is_trusted(USER_ID, second)) is False


def test_remember_window_uses_the_configured_hours():
    with settings(security_2fa_remember_hours="8"):
        assert security_settings.two_factor_remember_hours() == 8
    assert security_settings.two_factor_remember_hours() == 48   # default


# ── login: forced enrolment / challenge / trusted skip ─────────────────────

def _call_login(monkeypatch, user, *, trusted=False, **settings_overrides):
    _login_stubs(monkeypatch, user)
    monkeypatch.setattr(auth, "verify_password", lambda plain, hashed: True)
    monkeypatch.setattr(auth.two_factor, "is_trusted", AsyncMock(return_value=trusted))
    with settings(password_attempts="false", **settings_overrides):
        return asyncio.run(auth.login(
            request=_request("/api/v1/auth/login"),
            response=Response(),
            credentials=UserLogin(email=user.email, password="whatever"),
            background_tasks=None,
            db=object(),
        ))


def test_login_withholds_the_session_until_an_admin_enrols(monkeypatch):
    """2FA on, admin-tier, no factor -> no cookies at all, just an enrol challenge."""
    body = _call_login(monkeypatch, _admin_user(), security_2fa="true")

    assert body.status_code == 200
    assert body.headers.get("set-cookie") is None    # no session may exist yet
    payload = json.loads(body.body)
    assert payload["two_factor_required"] is True
    assert payload["enrolled"] is False
    assert payload["challenge_token"]


def test_login_challenges_an_already_enrolled_admin(monkeypatch):
    body = _call_login(
        monkeypatch, _admin_user(two_factor_method="totp"), security_2fa="true",
    )
    assert body.headers.get("set-cookie") is None
    payload = json.loads(body.body)
    assert payload["enrolled"] is True
    assert payload["method"] == "totp"


def test_login_skips_the_factor_for_a_trusted_device(monkeypatch):
    response = Response()
    _login_stubs(monkeypatch, _admin_user(two_factor_method="email"))
    monkeypatch.setattr(auth, "verify_password", lambda plain, hashed: True)
    monkeypatch.setattr(auth.two_factor, "is_trusted", AsyncMock(return_value=True))

    with settings(password_attempts="false", security_2fa="true"):
        body = asyncio.run(auth.login(
            # The trust cookie is what makes the device recognisable.
            request=_request("/api/v1/auth/login", cookies={"2fa_trust": "remembered-token"}),
            response=response,
            credentials=UserLogin(email="admin@burncost.com", password="whatever"),
            background_tasks=None,
            db=object(),
        ))

    assert body["two_factor_required"] is False
    cookies = response.headers.getlist("set-cookie")
    assert any(c.startswith("access_token=") for c in cookies)


def test_login_leaves_non_admin_roles_untouched_by_2fa(monkeypatch):
    """The policy is scoped to console tiers - buyers must not be challenged."""
    buyer = _admin_user(role=SimpleNamespace(value="customer"))
    body = _call_login(monkeypatch, buyer, security_2fa="true")
    assert body["two_factor_required"] is False
    assert body["role"] == "customer"


# ── admin guard: the tfa claim closes the pre-existing-session bypass ──────

def test_admin_guard_rejects_a_session_minted_before_2fa_was_enabled():
    """A token with no `tfa` claim must not reach the console once the policy is on."""
    user = _admin_user(two_factor_method="totp")
    legacy = create_access_token(data={"sub": USER_ID, "role": "admin"})  # no tfa claim

    with settings(security_2fa="true"):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(deps.enforce_admin_access(request=_request(token=legacy), current_user=user))
    assert caught.value.status_code == 403
    assert caught.value.detail["code"] == "two_factor_required"


def test_admin_guard_rejects_an_unenrolled_admin_even_with_a_claim():
    user = _admin_user(two_factor_method=None)
    forged = create_access_token(data={"sub": USER_ID, "role": "admin", "tfa": True})

    with settings(security_2fa="true"):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(deps.enforce_admin_access(request=_request(token=forged), current_user=user))
    assert caught.value.detail["code"] == "two_factor_required"


def test_admin_guard_admits_a_verified_session():
    user = _admin_user(two_factor_method="totp")
    token = create_access_token(data={"sub": USER_ID, "role": "admin", "tfa": True})

    with settings(security_2fa="true"):
        assert asyncio.run(deps.enforce_admin_access(request=_request(token=token), current_user=user)) is user


def test_admin_guard_ignores_non_admin_roles_entirely():
    user = _admin_user(role=SimpleNamespace(value="vendor"))
    with settings(security_2fa="true"):
        assert asyncio.run(deps.enforce_admin_access(request=_request(), current_user=user)) is user


# ── refresh must carry the claim (or every refreshed admin is bounced) ─────

def _refresh_access_token(monkeypatch, refresh_token: str, user) -> dict:
    # `auth.redis_client` (not security_settings) is what the refresh path uses.
    monkeypatch.setattr(auth, "redis_client", StubRedis({f"refresh:{USER_ID}": refresh_token}))
    monkeypatch.setattr(auth.user_crud, "get", AsyncMock(return_value=user))
    response = Response()
    with settings(security_2fa="true"):
        asyncio.run(auth.refresh_token(
            request=_request("/api/v1/auth/refresh", token=refresh_token),
            response=response,
            db=object(),
            token_data=None,
        ))
    cookie = next(c for c in response.headers.getlist("set-cookie") if c.startswith("access_token="))
    return decode_token(cookie.split("access_token=", 1)[1].split(";", 1)[0])


def test_refresh_preserves_the_tfa_claim(monkeypatch):
    user = _admin_user(two_factor_method="totp")
    token = create_refresh_token(data={"sub": USER_ID, "role": "admin", "tfa": True})
    assert _refresh_access_token(monkeypatch, token, user)["tfa"] is True


def test_refresh_never_upgrades_an_unverified_session(monkeypatch):
    """A legacy refresh token (no `tfa`) must not be refreshed into a verified one."""
    user = _admin_user(two_factor_method="totp")
    token = create_refresh_token(data={"sub": USER_ID, "role": "admin"})
    assert _refresh_access_token(monkeypatch, token, user)["tfa"] is False


# ── PUT /admin/settings: allowlist + lockout-prevention validation ─────────

class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return []


class _StubDB:
    """Just enough of AsyncSession for the settings upsert."""

    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.commits = 0

    async def execute(self, *args, **kwargs):
        return _StubResult(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _put_settings(payload, monkeypatch):
    from app.api.v1.endpoints import admin_settings

    # Deterministic: the real env may or may not have a mail provider configured.
    monkeypatch.setattr(admin_settings, "_email_provider_configured", lambda: False)
    db = _StubDB(existing=None)
    result = asyncio.run(admin_settings.admin_update_settings(
        payload=payload,
        current_user=SimpleNamespace(id=USER_ID, role="admin"),
        db=db,
    ))
    return result, db


def test_settings_write_rejects_an_unknown_key(monkeypatch):
    with pytest.raises(HTTPException) as caught:
        _put_settings({"settings": {"not_a_real_key": "true"}}, monkeypatch)
    assert caught.value.status_code == 400
    assert "not_a_real_key" in str(caught.value.detail)


def test_settings_write_rejects_email_otp_without_a_mail_provider(monkeypatch):
    with pytest.raises(HTTPException) as caught:
        _put_settings(
            {"settings": {"security_2fa": "true", "security_2fa_default_method": "email"}},
            monkeypatch,
        )
    assert caught.value.status_code == 400
    assert "email provider" in str(caught.value.detail).lower()


def test_settings_write_rejects_ip_whitelisting_with_an_empty_allowlist(monkeypatch):
    with pytest.raises(HTTPException) as caught:
        _put_settings({"settings": {"security_ip": "true", "security_ip_allowlist": "  "}}, monkeypatch)
    assert caught.value.status_code == 400
    assert "allowlist" in str(caught.value.detail).lower()


def test_settings_write_normalises_booleans_and_persists(monkeypatch):
    result, db = _put_settings(
        {"settings": {"security_2fa": "true", "security_2fa_default_method": "totp"},
         "section": "security"},
        monkeypatch,
    )
    assert result["section"] == "security"
    assert result["updated"] == 2
    stored = {row.key: row.value for row in db.added}
    assert stored["security_2fa"] == "true"          # canonical flag string
    assert stored["security_2fa_default_method"] == "totp"
    assert db.commits == 1






