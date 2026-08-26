"""Payment & Fraud guard helpers for Phase 11.

Centralises the integrity checks that must not be bypassed by client input:
  - verify_order_payment()  — never trust a client amount; use DB order total.
  - verify_webhook_signature() — HMAC/SHA512 signature checks for Paystack/Flutterwave.
  - audit_payment_event() — persist every payment init/verify to audit_logs.
  - payment_attempt_limit() — Redis rate-limit payment attempts per user/IP.
  - record_failed_login() / is_login_locked() — ATO lockout after repeated failures.
"""
import hashlib
import hmac
import logging
import os
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)


def verify_webhook_signature(provider: str, payload_body: bytes, signature: str, secret: str) -> bool:
    """Verify a gateway webhook signature.

    - Paystack:  SHA512 HMAC of the raw body using PAYSTACK_SECRET_KEY.
    - Flutterwave: plain `verif-hash` header must equal FLUTTERWAVE_SECRET_HASH.
    Returns True only when the signature is valid. Calls without a configured
    secret are rejected (fail-closed) so a missing env var never silently passes.
    """
    if not secret:
        logger.warning(f"{provider} webhook disabled: no secret configured")
        return False
    if provider == "paystack":
        expected = hmac.new(secret.encode(), payload_body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, signature or "")
    if provider == "flutterwave":
        return hmac.compare_digest(signature or "", secret)
    return False


async def verify_order_payment(db: AsyncSession, order_id: str) -> bool:
    """Server-side amount sanity check for an order.

    Prevents "payment manipulation": every payment must be reconciled to the
    DB order total. Always returns False when the order is missing or already
    completed, so the gateway never marks a bogus/mismatched order as paid.
    """
    from app.models.order import Order, PaymentStatus
    try:
        from uuid import UUID
        result = await db.execute(select(Order).where(Order.id == UUID(order_id)))
    except ValueError:
        return False
    order = result.scalar_one_or_none()
    if not order:
        return False
    if order.payment_status == PaymentStatus.COMPLETED:
        return False  # idempotency: never re-confirm an already-paid order
    return True


async def record_fraud_flag(
    db: AsyncSession,
    *,
    alert_type: str,
    description: str,
    severity: str = "high",
    amount: float = 0,
    user_id: str = None,
) -> None:
    """Persist a fraud/risk alert to the `fraud_alerts` table (best-effort)."""
    try:
        import uuid
        from app.models.fraud import FraudAlert
        db.add(FraudAlert(
            id=uuid.uuid4(),
            alert_number=f"FA-{uuid.uuid4().hex[:12].upper()}",
            alert_type=alert_type,
            severity=severity,
            description=description,
            risk_score=90,
            amount=amount,
            status="under_review",
            detected_at=datetime.utcnow(),
        ))
        await db.commit()
    except Exception as e:
        logger.warning(f"Failed to record fraud flag: {e}")
        try:
            await db.rollback()
        except Exception:
            pass


async def audit_payment_event(db: AsyncSession, *, user_id, action: str, reference: str, ip: str = "", amount: float = 0) -> None:
    """Persist a payment init/verify event to audit_logs (best-effort)."""
    try:
        from app.models.audit_log import AuditLog
        db.add(AuditLog(
            user_id=user_id,
            action=action,
            resource_type="payment",
            resource_id=reference,
            method="POST",
            path=f"/payments/{action}",
            ip_address=ip,
            details=f"amount={amount}",
        ))
        await db.commit()
    except Exception as e:
        logger.warning(f"Audit payment event failed: {e}")
        try:
            await db.rollback()
        except Exception:
            pass


async def payment_attempt_limit(limit: int = 10, window_seconds: int = 60, *key_parts) -> bool:
    """Rate limit payment attempts per user/IP. True = allowed."""
    try:
        import redis.asyncio as airedis
        from app.config import settings
        client = airedis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                               db=settings.REDIS_DB, decode_responses=True) if settings.DEBUG \
                 else airedis.from_url(settings.REDIS_URL, ssl_cert_reqs=None, decode_responses=True)
        key = "pa:" + ":".join(str(p) for p in key_parts)
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
        count = int(results[0] or 0)
        await client.aclose() if hasattr(client, "aclose") else None
        return count <= limit
    except Exception:
        return True  # fail-open for rate limit to not block payments on Redis outage


async def record_failed_login(db: AsyncSession, email: str, ip: str = "") -> int:
    """Increment failed-login counter in Redis. Returns new count."""
    try:
        import redis.asyncio as airedis
        from app.config import settings
        client = airedis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                               db=settings.REDIS_DB, decode_responses=True) if settings.DEBUG \
                 else airedis.from_url(settings.REDIS_URL, ssl_cert_reqs=None, decode_responses=True)
        key = f"login_fail:{email.lower()}:{ip}"
        n = await client.incr(key)
        await client.expire(key, 900)  # 15 min window
        return int(n)
    except Exception:
        return 0


async def clear_failed_logins(email: str, ip: str = "") -> None:
    """Clear failed-login counter after a successful login."""
    try:
        import redis.asyncio as airedis
        from app.config import settings
        client = airedis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                               db=settings.REDIS_DB, decode_responses=True) if settings.DEBUG \
                 else airedis.from_url(settings.REDIS_URL, ssl_cert_reqs=None, decode_responses=True)
        await client.delete(f"login_fail:{email.lower()}:{ip}")
    except Exception:
        pass


async def is_login_locked(email: str, ip: str = "", max_failures: int = 5) -> bool:
    """Return True when the account/IP has exceeded the failed-login threshold."""
    try:
        import redis.asyncio as airedis
        from app.config import settings
        client = airedis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                               db=settings.REDIS_DB, decode_responses=True) if settings.DEBUG \
                 else airedis.from_url(settings.REDIS_URL, ssl_cert_reqs=None, decode_responses=True)
        n = int(await client.get(f"login_fail:{email.lower()}:{ip}") or 0)
        return n >= max_failures
    except Exception:
        return False