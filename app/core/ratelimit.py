"""Shared Redis rate-limiting helpers (Phase 10).

Provides per-IP and per-user rate limiting using the Redis connection that the
app already uses for refresh-token storage. Failures degrade gracefully to
"allow" so rate limiting never breaks an otherwise-healthy request path.
"""
import logging
import time

logger = logging.getLogger(__name__)


def _get_redis():
    """Return a shared Redis client, or None when unavailable."""
    try:
        import redis.asyncio as airedis
        from app.config import settings
        if settings.DEBUG:
            return airedis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                decode_responses=True,
            )
        return airedis.from_url(settings.REDIS_URL, ssl_cert_reqs=None, decode_responses=True)
    except Exception as e:
        logger.warning(f"Rate-limit redis unavailable: {e}")
        return None


async def rate_limit(limit: int, window_seconds: int, *key_parts) -> bool:
    """Increment a counter and return True if the request is allowed.

    Uses a Redis INCR + EXPIRE pipeline keyed by the provided parts.
    Returns True (allow) when Redis is unavailable or when under the limit.
    """
    _redis = _get_redis()
    if _redis is None:
        return True
    key = "rl:" + ":".join(str(p) for p in key_parts)
    try:
        pipe = _redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
        count = int(results[0] or 0)
        return count <= limit
    except Exception as e:
        logger.warning(f"Rate-limit check failed for {key}: {e}")
        return True