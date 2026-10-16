"""Second-factor primitives for admin 2FA (Email OTP / authenticator app).

Two methods are supported per admin, chosen at enrolment:

* ``email`` - a 6-digit code is emailed and verified (stored hashed in Redis).
* ``totp``  - an authenticator app, provisioned from a server-rendered QR code
  (no new Admin npm dependency) plus 10 single-use recovery codes.

Secrets are encrypted at rest with a key derived from ``SECRET_KEY`` and the
"remember this device" window is a Redis-backed trust token, so no extra table
is needed for either. Every Redis interaction is best-effort: callers decide
whether the failure should fail open or closed (the guard fails closed).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5
OTP_DIGITS = 6
RECOVERY_CODE_COUNT = 10
TOTP_ISSUER = "Burncost Admin"


# ── secret encryption (Fernet, key derived from SECRET_KEY) ────────────────

def _fernet():
    from cryptography.fernet import Fernet

    digest = hashlib.sha256((settings.SECRET_KEY or "").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: Optional[str]) -> Optional[str]:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except Exception as exc:  # pragma: no cover - key rotation / corruption
        logger.error("Failed to decrypt TOTP secret: %s", exc)
        return None


# ── authenticator app (TOTP) ───────────────────────────────────────────────

def generate_totp_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    import pyotp

    return pyotp.TOTP(secret).provisioning_uri(name=email or "admin", issuer_name=TOTP_ISSUER)


def qr_data_uri(uri: str) -> str:
    """Render `uri` as a PNG data-URI so the Admin app needs no QR dependency."""
    import base64 as _b64
    from io import BytesIO

    import qrcode

    img = qrcode.make(uri)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()


def verify_totp(secret: Optional[str], code: str) -> bool:
    """Verify a 6-digit TOTP, tolerating one step of clock drift either way."""
    if not secret:
        return False
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    import pyotp

    try:
        return bool(pyotp.TOTP(secret).verify(code, valid_window=1))
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("TOTP verify failed: %s", exc)
        return False


# ── recovery codes ─────────────────────────────────────────────────────────

def _hash_code(code: str) -> str:
    return hashlib.sha256((code or "").strip().upper().encode()).hexdigest()


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Fresh single-use codes in a copy-friendly `XXXX-XXXX` shape."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1 ambiguity
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def hash_recovery_codes(codes: list[str]) -> str:
    return json.dumps([_hash_code(c) for c in codes])


def verify_and_consume_recovery_code(stored_json: Optional[str], code: str) -> tuple[bool, Optional[str]]:
    """Check `code`, returning ``(matched, remaining_hashes_json)``.

    A match removes that code so it can never be replayed.
    """
    if not stored_json:
        return False, stored_json
    try:
        hashes = json.loads(stored_json)
        if not isinstance(hashes, list):
            return False, stored_json
    except (ValueError, TypeError):
        return False, stored_json

    target = _hash_code(code)
    remaining = [h for h in hashes if not hmac.compare_digest(h, target)]
    if len(remaining) == len(hashes):
        return False, stored_json
    return True, json.dumps(remaining)


# ── email OTP (Redis-backed, hashed) ───────────────────────────────────────

def _now() -> int:
    return int(time.time())


async def issue_email_otp(user_id: str) -> Optional[str]:
    """Generate + store a one-time code for `user_id`. Returns the plaintext code."""
    from app.services import security_settings

    client = security_settings.get_redis()
    code = "".join(secrets.choice("0123456789") for _ in range(OTP_DIGITS))
    if client is None:
        # Without Redis we cannot verify anything, so hand back a code the caller
        # must not email (verification will fail closed).
        logger.error("Redis unavailable - email OTP cannot be issued")
        return None
    try:
        await client.set(f"2fa_otp:{user_id}", _hash_code(code), ex=OTP_TTL_SECONDS)
        await client.delete(f"2fa_otp_attempts:{user_id}")
    except Exception as exc:
        logger.error("Failed to store email OTP: %s", exc)
        return None
    return code


async def verify_email_otp(user_id: str, code: str) -> bool:
    """Check an emailed code, consuming it on success and rate-limiting attempts."""
    from app.services import security_settings

    client = security_settings.get_redis()
    if client is None:
        return False
    try:
        attempts = int(await client.get(f"2fa_otp_attempts:{user_id}") or 0)
        if attempts >= OTP_MAX_ATTEMPTS:
            return False
        stored = await client.get(f"2fa_otp:{user_id}")
        if stored and hmac.compare_digest(str(stored), _hash_code(code)):
            await client.delete(f"2fa_otp:{user_id}", f"2fa_otp_attempts:{user_id}")
            return True
        await client.incr(f"2fa_otp_attempts:{user_id}")
        await client.expire(f"2fa_otp_attempts:{user_id}", OTP_TTL_SECONDS)
        return False
    except Exception as exc:
        logger.error("Email OTP verification failed: %s", exc)
        return False


async def otp_is_pending(user_id: str) -> bool:
    from app.services import security_settings

    client = security_settings.get_redis()
    if client is None:
        return False
    try:
        return bool(await client.exists(f"2fa_otp:{user_id}"))
    except Exception:
        return False


# ── challenge token (password verified, factor pending) ────────────────────

def new_challenge_token(user_id: str, purpose: str) -> str:
    """Short-lived JWT proving a password was verified but the factor was not."""
    from app.core.security import create_access_token

    return create_access_token(
        data={"sub": str(user_id), "type": "2fa_challenge", "purpose": purpose},
        expires_delta=timedelta(minutes=10),
    )


def decode_challenge_token(token: str, purpose: str) -> Optional[str]:
    from app.core.security import decode_token

    try:
        payload = decode_token(token)
    except Exception:
        return None
    if payload.get("type") != "2fa_challenge" or payload.get("purpose") != purpose:
        return None
    return payload.get("sub")


# ── "remember this device" trust ───────────────────────────────────────────

def _trust_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


async def create_trust(user_id: str, hours: int, *, user_agent: str = "", ip: str = "",
                       method: str = "") -> Optional[str]:
    """Mint a trust token for this device; returns the raw token (or None)."""
    from app.services import security_settings

    client = security_settings.get_redis()
    if client is None:
        return None
    token = secrets.token_urlsafe(32)
    token_hash = _trust_hash(token)
    record = json.dumps({
        "user_id": str(user_id),
        "created_at": _now(),
        "expires_at": _now() + hours * 3600,
        "user_agent": (user_agent or "")[:300],
        "ip": ip or "",
        "method": method or "",
    })
    try:
        await client.set(f"2fa_trust:{token_hash}", record, ex=hours * 3600)
        await client.sadd(f"2fa_trust_user:{user_id}", token_hash)
        await client.expire(f"2fa_trust_user:{user_id}", hours * 3600 * 2)
    except Exception as exc:
        logger.error("Failed to create 2FA trust token: %s", exc)
        return None
    return token


async def is_trusted(user_id: str, token: Optional[str]) -> bool:
    """True while `token` is a live trust entry for `user_id`."""
    if not token:
        return False
    from app.services import security_settings

    client = security_settings.get_redis()
    if client is None:
        return False
    try:
        raw = await client.get(f"2fa_trust:{_trust_hash(token)}")
        if not raw:
            return False
        record = json.loads(raw)
        return str(record.get("user_id")) == str(user_id)
    except Exception:
        return False


async def list_devices(user_id: str) -> list[dict]:
    """Remembered devices for `user_id`, newest first (self-expiring entries)."""
    from app.services import security_settings

    client = security_settings.get_redis()
    if client is None:
        return []
    try:
        hashes = list(await client.smembers(f"2fa_trust_user:{user_id}") or [])
        devices: list[dict] = []
        for token_hash in hashes:
            raw = await client.get(f"2fa_trust:{token_hash}")
            if not raw:
                await client.srem(f"2fa_trust_user:{user_id}", token_hash)
                continue
            record = json.loads(raw)
            devices.append({
                "id": token_hash,
                "created_at": record.get("created_at"),
                "expires_at": record.get("expires_at"),
                "user_agent": record.get("user_agent") or "",
                "ip": record.get("ip") or "",
                "method": record.get("method") or "",
            })
        devices.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
        return devices
    except Exception as exc:
        logger.debug("Failed to list 2FA devices: %s", exc)
        return []


async def revoke_device(user_id: str, token_hash: str) -> bool:
    """Revoke one remembered device (id comes from `list_devices`)."""
    from app.services import security_settings

    client = security_settings.get_redis()
    if client is None:
        return False
    try:
        removed = await client.delete(f"2fa_trust:{token_hash}")
        await client.srem(f"2fa_trust_user:{user_id}", token_hash)
        return bool(removed)
    except Exception as exc:
        logger.debug("Failed to revoke 2FA device: %s", exc)
        return False


async def revoke_all(user_id: str) -> None:
    """Drop every remembered device for `user_id` (password change / disable)."""
    from app.services import security_settings

    client = security_settings.get_redis()
    if client is None:
        return
    try:
        hashes = list(await client.smembers(f"2fa_trust_user:{user_id}") or [])
        if hashes:
            await client.delete(*[f"2fa_trust:{h}" for h in hashes])
        await client.delete(f"2fa_trust_user:{user_id}")
    except Exception as exc:
        logger.debug("Failed to revoke all 2FA devices: %s", exc)



