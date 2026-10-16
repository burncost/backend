"""Redis pub/sub bridge for live delivery tracking (SSE fan-out).

A driver's location heartbeat publishes one small JSON message to a per-job
channel; the SSE endpoint (`/delivery/jobs/{id}/stream`) subscribes to that
channel and forwards each message to the watching buyer/vendor browser.

Everything here is best-effort by design: when Redis is unavailable the
publisher silently no-ops and the subscriber ends immediately, leaving the
client's polling fallback (`/delivery/jobs/{id}/track`) to keep the map live.
A tracking feature must never fail a location heartbeat or an order page.
"""
import asyncio
import json
import logging
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


def job_channel(job_id) -> str:
    """Pub/sub channel carrying position beats for one delivery job."""
    return f"delivery:job:{job_id}:loc"


_client = None


def _get_redis():
    """Reuse a cached Redis client (same settings/connection as ratelimit)."""
    global _client
    if _client is not None:
        return _client
    try:
        from app.core.ratelimit import _get_redis as _new_client
        _client = _new_client()  # cached only when it actually connected
        return _client
    except Exception as e:  # pragma: no cover - import/env problems only
        logger.warning("Live events: Redis client unavailable: %s", e)
        return None


async def publish_job_location(job_id, payload: dict) -> None:
    """Publish one position beat. Never raises (a heartbeat must not fail)."""
    client = _get_redis()
    if client is None:
        return
    try:
        await client.publish(job_channel(job_id), json.dumps(payload, default=str))
    except Exception as e:
        logger.warning("Live publish failed for job %s: %s", job_id, e)


async def iter_job_locations(
    job_id,
    *,
    idle_timeout: float = 15.0,
    max_seconds: float = 900.0,
) -> AsyncIterator[Optional[str]]:
    """Yield raw JSON strings for a job's location updates.

    Yields `None` when idle so the caller can emit an SSE keep-alive comment
    (proxies drop a silent stream). Returns once `max_seconds` elapses so the
    browser reconnects on a fresh request instead of a socket being held open
    indefinitely.
    """
    client = _get_redis()
    if client is None:
        return
    pubsub = client.pubsub()
    channel = job_channel(job_id)
    try:
        await pubsub.subscribe(channel)
    except Exception as e:
        logger.warning("Live subscribe failed for job %s: %s", job_id, e)
        return

    loop = asyncio.get_event_loop()
    started = loop.time()
    try:
        while True:
            if loop.time() - started > max_seconds:
                return
            try:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=idle_timeout
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Live stream read failed for job %s: %s", job_id, e)
                return
            if msg and msg.get("type") == "message":
                yield msg.get("data")
            else:
                yield None
    finally:
        # Tear the subscription down on disconnect, timeout or client cancel.
        for closer in ("unsubscribe", "aclose", "close"):
            fn = getattr(pubsub, closer, None)
            if fn is None:
                continue
            try:
                await fn(channel) if closer == "unsubscribe" else await fn()
            except Exception:
                pass
