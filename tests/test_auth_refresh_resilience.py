"""
Refresh/login resilience when Redis is unreachable.

Regression guard for the "UI shows HTTP 401" bug: `/auth/refresh` called Redis
with no error handling, so a Redis blip (ConnectionResetError from a stale
pooled socket, or a connect timeout) escaped as an unhandled 500. A healthy
user's session then looked broken to the client.

The contract these tests pin down:
  * Redis down  -> 503 (transient, retryable) - NEVER 401, so the client keeps
    its refresh cookie and simply retries.
  * Redis fine  -> refresh still works and the token is rotated.
  * A genuinely bad/unknown refresh token still gets a 401 (no loosening).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import redis.exceptions as redis_exc
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.v1.endpoints import auth
from app.core.security import create_refresh_token, decode_token, get_password_hash
from app.schemas.user import UserLogin

USER_ID = "11111111-1111-1111-1111-111111111111"


# ── test doubles ─────────────────────────────────────────────────────────────

class _BrokenRedis:
    """Raises the two failures seen in the wild for every command."""

    def __init__(self, exc):
        self._exc = exc

    async def get(self, *args, **kwargs):
        raise self._exc

    async def set(self, *args, **kwargs):
        raise self._exc


class _StubRedis:
    """Minimal in-memory stand-in for the two calls the auth flow makes."""

    def __init__(self, initial=None):
        self.store = dict(initial or {})

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True


def _request(authorization: str | None = None) -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/auth/refresh",
        "raw_path": b"/api/v1/auth/refresh",
        "query_string": b"",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _call_refresh(token: str):
    # token_data is passed explicitly: calling the handler directly bypasses
    # FastAPI's Body(default=None) resolution, which would otherwise hand us the
    # raw `Body` sentinel object instead of None.
    return asyncio.run(
        auth.refresh_token(
            request=_request(f"Bearer {token}"),
            response=Response(),
            db=object(),
            token_data=None,
        )
    )


# ── /auth/refresh: Redis outage is transient, not a dead session ─────────────

@pytest.mark.parametrize(
    "exc",
    [
        redis_exc.ConnectionError("Connection reset by peer"),
        redis_exc.TimeoutError("Timeout connecting to server"),
    ],
)
def test_refresh_returns_503_not_500_when_redis_is_down(monkeypatch, exc):
    monkeypatch.setattr(auth, "redis_client", _BrokenRedis(exc))
    token = create_refresh_token(data={"sub": USER_ID})

    with pytest.raises(HTTPException) as caught:
        _call_refresh(token)

    assert caught.value.status_code == 503
    # 401 would make the client treat a live session as dead and sign the user out.
    assert caught.value.status_code != 401
    assert "try again" in str(caught.value.detail).lower()


def test_refresh_returns_503_when_token_rotation_fails(monkeypatch):
    """The lookup succeeds but the store fails - still a retryable 503."""
    token = create_refresh_token(data={"sub": USER_ID})

    class LookupOkStoreBroken(_StubRedis):
        async def set(self, *args, **kwargs):
            raise redis_exc.ConnectionError("Connection reset by peer")

    monkeypatch.setattr(
        auth, "redis_client", LookupOkStoreBroken({f"refresh:{USER_ID}": token})
    )
    monkeypatch.setattr(
        auth.user_crud, "get", AsyncMock(return_value=SimpleNamespace(id=USER_ID, role="customer"))
    )

    with pytest.raises(HTTPException) as caught:
        _call_refresh(token)

    assert caught.value.status_code == 503


# ── /auth/refresh: the healthy path still works ──────────────────────────────

def test_refresh_rotates_token_when_redis_is_healthy(monkeypatch):
    token = create_refresh_token(data={"sub": USER_ID})
    redis_stub = _StubRedis({f"refresh:{USER_ID}": token})
    monkeypatch.setattr(auth, "redis_client", redis_stub)
    monkeypatch.setattr(
        auth.user_crud, "get", AsyncMock(return_value=SimpleNamespace(id=USER_ID, role="customer"))
    )

    body = asyncio.run(
        auth.refresh_token(
            request=_request(f"Bearer {token}"),
            response=Response(),
            db=object(),
            token_data=None,
        )
    )

    assert body["role"] == "customer"
    assert body["expires_in"] > 0
    # The presented refresh token must have been (re)stored for the user.
    # It is a *rotated* token: since refresh tokens now carry the `role`/`tfa`
    # claims (so a refreshed access token keeps its security posture) the minted
    # string always differs, so assert on what was stored and what it decodes to
    # rather than on it matching the presented token byte-for-byte.
    stored = redis_stub.store[f"refresh:{USER_ID}"]
    assert stored
    assert decode_token(stored)["sub"] == USER_ID
    assert decode_token(stored)["type"] == "refresh"
    assert decode_token(token)["sub"] == USER_ID


def test_refresh_rejects_a_token_that_redis_does_not_know(monkeypatch):
    monkeypatch.setattr(auth, "redis_client", _StubRedis())
    token = create_refresh_token(data={"sub": USER_ID})

    with pytest.raises(HTTPException) as caught:
        _call_refresh(token)

    assert caught.value.status_code == 401
    assert "expired" in str(caught.value.detail).lower()


def test_refresh_rejects_a_garbage_token(monkeypatch):
    monkeypatch.setattr(auth, "redis_client", _StubRedis())

    with pytest.raises(HTTPException) as caught:
        _call_refresh("not-a-jwt")

    assert caught.value.status_code == 401


def test_refresh_without_any_token_is_still_401(monkeypatch):
    monkeypatch.setattr(auth, "redis_client", _StubRedis())

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            auth.refresh_token(
                request=_request(), response=Response(), db=object(), token_data=None
            )
        )

    assert caught.value.status_code == 401


# ── /auth/login: a Redis outage answers 503, not 500 ─────────────────────────

def test_login_returns_503_when_redis_is_down(monkeypatch):
    password = "CorrectHorseBattery1!"
    user = SimpleNamespace(
        id=USER_ID,
        role="customer",
        status="active",
        password_hash=get_password_hash(password),
        profile=None,
    )
    monkeypatch.setattr(auth, "redis_client", _BrokenRedis(redis_exc.ConnectionError("down")))
    monkeypatch.setattr(auth, "is_login_locked", AsyncMock(return_value=False))
    monkeypatch.setattr(auth.user_crud, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(auth.vendor_crud, "get_by_user_id", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            auth.login(
                request=_request(),
                response=Response(),
                credentials=UserLogin(email="user@example.com", password=password),
                background_tasks=None,
                db=object(),
            )
        )

    assert caught.value.status_code == 503
    assert "try again" in str(caught.value.detail).lower()
