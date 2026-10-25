"""Persist an observed online price into `material_rates` as a PROVISIONAL row.

Why: when neither the vendor catalogue nor `material_rates` could price a BOQ
line, the pipeline falls back to a live online listing or the observed online
average reference. That lookup is expensive and network-dependent, so the next
run should be able to reuse it - without ever pretending a scraped figure is a
curated market rate.

Invariants (see tests/test_online_rate_writeback.py):
  * Only `online_market` / `online_average` observations are stored. An
    `ai_estimate` is a model guess and is refused outright.
  * A stored observation is `is_provisional = True`, `verified_at = NULL` and
    keeps its `source_url`/`confidence`/`observed_at` so the admin UI can show
    where it came from.
  * A curated row (manual/api/scraped, is_provisional=False) is NEVER
    overwritten - it is left exactly as it was.
  * Upserts are keyed on `sha1(normalize(name)|unit|state)`, so re-observing the
    same material updates one row (and appends price history) instead of
    duplicating the table.
  * Every failure is logged and swallowed: a cache-write problem must never fail
    the BOQ or verification request it was helping.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.material_rate import MaterialRate, MaterialRateHistory
from app.services.material_rate_sync import (
    PERSISTABLE_SOURCES,
    find_cached_rate,
    get_or_create_online_category,
    normalize_product_name,
    normalize_unit,
    rate_lookup_key,
    state_for_city,
    utcnow,
)

logger = logging.getLogger(__name__)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def persist_online_price(
    *,
    product_name: str,
    unit: Optional[str],
    city: Optional[str],
    price: Any,
    source: str,
    confidence: Any = None,
    source_url: Optional[str] = None,
    specification: Optional[str] = None,
    observed_at: Optional[datetime] = None,
    session: Optional[AsyncSession] = None,
) -> Optional[Dict[str, Any]]:
    """Upsert one online observation as a provisional material rate.

    Returns a small status dict (`created` / `updated` / `skipped_*`) or None
    when write-back is disabled or the observation is not persistable.
    """
    if not getattr(settings, "ONLINE_PRICE_WRITEBACK_ENABLED", False):
        return None
    if source not in PERSISTABLE_SOURCES:
        return None

    name = (product_name or "").strip()
    amount = _as_float(price)
    if not name or amount <= 0:
        return None

    confidence_value = _as_float(confidence, 0.0)
    floor = _as_float(
        getattr(settings, "ONLINE_PRICE_WRITEBACK_MIN_CONFIDENCE", 0.0), 0.0
    )
    if confidence_value < floor:
        return None

    state = state_for_city(city)
    key = rate_lookup_key(name, unit, state)
    observed = observed_at or utcnow()

    owns_session = session is None
    db = session
    try:
        if db is None:
            from app.core.database import AsyncSessionLocal

            db = AsyncSessionLocal()

        existing = await find_cached_rate(db, key)
        if existing is None:
            # Legacy rows predate `lookup_key`, so the natural-key lookup misses
            # them. Match them by (normalised name, unit, state) before inserting
            # - otherwise a curated rate would be duplicated by a provisional one
            # and could later be picked as the "database" price.
            legacy = (await db.execute(
                select(MaterialRate).where(
                    MaterialRate.lookup_key.is_(None),
                    MaterialRate.state == state,
                )
            )).scalars().all()
            wanted_name = normalize_product_name(name)
            wanted_unit = normalize_unit(unit)
            for candidate in legacy:
                if (
                    normalize_product_name(candidate.material_name) == wanted_name
                    and normalize_unit(candidate.unit) == wanted_unit
                ):
                    existing = candidate
                    break

        if existing is not None and not bool(existing.is_provisional):
            # Curated data wins. Never let a web observation relabel it.
            logger.info(
                "Online write-back skipped: '%s' is a curated rate (%s)",
                name, existing.source,
            )
            if owns_session:
                await db.rollback()
            return {"status": "skipped_curated", "rate_id": str(existing.id)}

        if existing is None:
            category = await get_or_create_online_category(db)
            row = MaterialRate(
                category_id=category.id,
                material_name=name,
                specification=specification,
                unit=unit or "",
                current_price=amount,
                previous_price=None,
                currency="NGN",
                state=state,
                source=source,
                source_url=source_url,
                confidence=confidence_value or None,
                is_provisional=True,
                observed_at=observed,
                lookup_key=key,
            )
            db.add(row)
            await db.flush()
            status = "created"
        else:
            row = existing
            row.previous_price = row.current_price
            row.current_price = amount
            row.source = source
            row.source_url = source_url
            row.confidence = confidence_value or None
            row.observed_at = observed
            row.is_provisional = True
            # Adopt the natural key for legacy provisional rows so the next
            # observation finds this row directly.
            row.lookup_key = row.lookup_key or key
            # An admin promotion is authoritative; a later observation must not
            # silently un-verify it. This branch only runs while still
            # provisional, so `verified_at` is already NULL.
            row.verified_at = None
            status = "updated"

        db.add(MaterialRateHistory(
            rate_id=row.id,
            price=amount,
            recorded_at=observed,
            source=source,
        ))

        if owns_session:
            await db.commit()
        else:
            await db.flush()

        return {
            "status": status,
            "rate_id": str(row.id),
            "lookup_key": key,
            "state": state,
            "source": source,
            "confidence": confidence_value,
            "source_url": source_url,
        }
    except Exception as exc:  # cache write is best-effort, never fatal
        logger.warning("Online write-back failed for '%s': %s", name, exc)
        if owns_session and db is not None:
            try:
                await db.rollback()
            except Exception:
                pass
        return None
    finally:
        if owns_session and db is not None:
            await db.close()


async def persist_rate_observation(
    payload: Dict[str, Any],
    *,
    description: str,
    city: str,
    unit: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> Optional[Dict[str, Any]]:
    """Convenience wrapper for a `PriceService` online-tier payload.

    The payloads from `online_price_search` and `find_online_average` already
    carry provenance (`source`, `confidence`, `source_url`), so this just maps
    them onto the persistence contract above.
    """
    if not payload:
        return None
    source = str(payload.get("source") or payload.get("price_source") or "")
    product_name = str(payload.get("product_name") or "").strip() or description
    return await persist_online_price(
        product_name=product_name,
        unit=payload.get("unit") or unit,
        city=payload.get("city") or city,
        price=payload.get("rate"),
        source=source,
        confidence=payload.get("confidence"),
        source_url=payload.get("source_url") or None,
        specification=normalize_product_name(description) or None,
        session=session,
    )
