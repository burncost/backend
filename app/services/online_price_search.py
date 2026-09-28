from __future__ import annotations

"""
LIVE ONLINE PRICE SEARCH — last price source before a BOQ gives up.

The BOQ chain is strict and this module is only ever reached after BOTH of our
own stores came up empty:

  1. `products` (PostgreSQL) — vendor catalogue, discount price first
  2. `material_rates` (PostgreSQL `current_price`, then MongoDB)
  3. THIS MODULE — published online price lists + general web search
  4. `online_average_prices` — the offline observed reference (no network)
  5. flagged AI estimate

Rules enforced here (see tests/test_price_sourcing_chain.py):
  * Never reached before the database: callers only fall through on a DB miss.
  * Every figure carries provenance — price_source="online_market" plus the
    page/listing it came from, so an operator can check it. The `verified` flag
    is left blank on this tier (it is only ever asserted by our own database).
  * Unit-safe: a live figure is only accepted when it states the same selling
    unit as the BOQ line, so a per-bag price can never land on an m2 line.
  * Never fatal: timeouts, HTTP errors, unparseable pages and model failures
    all return None, so the caller simply continues down the chain and the BOQ
    still totals.
  * Cached (in-process + MongoDB `online_price_cache`) and budgeted per run, so
    one BOQ cannot fan out into hundreds of live lookups.
  * A published price list is already a current market figure for the requested
    city, so no city multiplier is invented on top of it: the city is recorded
    on the payload instead.
"""
import asyncio
import html as html_lib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.services.online_average_prices import _normalize, normalize_unit

logger = logging.getLogger(__name__)

# Provenance labels used by every surface (API payload, BOQ document, UI).
LIVE_SOURCE = "online_market"
LIVE_LABEL = "Online market price"

# MongoDB collection holding fetched online prices between runs (cache only —
# deliberately NOT `material_rates`, so a scraped figure can never enter the
# verified-rate path used for quote inflation checks).
CACHE_COLLECTION = "online_price_cache"

# ── Page parsing ────────────────────────────────────────────────────────────

_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_HTML_TAG = re.compile(r"<[^>]+>")


def strip_html(raw: str) -> str:
    """Flatten a price-list page to text so rows can be read off it."""
    if not raw:
        return ""
    text = _SCRIPT_STYLE.sub(" ", str(raw))
    text = _HTML_TAG.sub("\n", text)
    # Every entity is decoded (stdlib table: &#8358; -> ₦, &nbsp; -> space),
    # because an undecoded "&nbsp;" carried a ";" that stopped a product name
    # from ever reaching its price.
    text = html_lib.unescape(text).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text)


# A label followed by an amount: "Cement 50kg bag — ₦14,000" / "Sand: NGN 70000
# per trip". Kept deliberately narrow so a phone number or a year is not read as
# a price.
_ROW_PATTERN = re.compile(
    r"(?P<label>[^\n|:;•]{3,90}?)\s*(?:[-–—:•|]{1,2})?\s*"
    r"(?<![A-Za-z0-9])(?P<currency>₦|NGN|N)\s*(?P<amount>\d[\d,]*(?:\.\d{1,2})?)"
    r"(?:\s*(?:/|per\s+)\s*(?P<unit>[A-Za-z0-9²³]{1,12}))?",
    re.I,
)

# Words that make a "label" a header or sentence rather than a product.
_LABEL_STOPWORDS = (
    "price list", "updated", "click", "read more", "about us", "contact",
    "total", "subtotal", "delivery fee", "vat", "terms",
)


def parse_price_rows(raw: str) -> List[Dict[str, Any]]:
    """Read (name, price, unit) rows off a price-list page.

    Pure and side-effect free so it is unit-testable without any network call.
    """
    text = strip_html(raw)
    rows: List[Dict[str, Any]] = []
    for match in _ROW_PATTERN.finditer(text):
        label = " ".join(match.group("label").split())
        if len(label) < 3:
            continue
        lowered = label.lower()
        if any(stop in lowered for stop in _LABEL_STOPWORDS):
            continue
        try:
            amount = float(match.group("amount").replace(",", ""))
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        rows.append(
            {
                "name": label,
                "price": amount,
                "unit": normalize_unit(match.group("unit") or "") or "",
            }
        )
    return rows


def match_price_row(
    rows: List[Dict[str, Any]], description: str, unit: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Cheapest row that matches the line item, or None.

    A row only qualifies when it shares at least one significant token with the
    line description AND its stated unit is the line's unit. Rows that state no
    unit are accepted only when the line's unit also appears inside the row
    label (e.g. row "Cement 50kg bag" for a line measured in `bag`), so a
    unit-less scraped figure can never be applied on trust.
    """
    text = _normalize(description)
    line_unit = normalize_unit(unit)
    if not text:
        return None

    tokens = [t for t in re.split(r"[^a-z0-9.]+", text) if len(t) >= 4]
    best: Optional[Dict[str, Any]] = None
    best_score = 0

    for row in rows:
        label = _normalize(row["name"])
        row_unit = normalize_unit(row.get("unit") or "")
        if line_unit and row_unit and row_unit != line_unit:
            continue
        if line_unit and not row_unit and not _unit_in_label(label, line_unit):
            continue

        score = sum(1 for token in tokens if token in label)
        if not score:
            continue
        if row_unit and line_unit and row_unit == line_unit:
            score += 2  # a stated, matching unit is the strongest signal

        if score > best_score or (
            score == best_score and best is not None and row["price"] < best["price"]
        ):
            best, best_score = row, score

    if best is None:
        return None
    return dict(best, match_score=best_score)


def _unit_in_label(label: str, unit: str) -> bool:
    """True when the row label itself states the unit (bag, m2, nr, ...)."""
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(unit) + r"(?![a-z0-9])", label))


# ── Fetching ────────────────────────────────────────────────────────────────

_USER_AGENT = "BurnCostPriceBot/1.0 (building-material price list reader)"


def price_list_sources() -> List[str]:
    """Configured published price-list pages, in order."""
    raw = settings.ONLINE_PRICE_SEARCH_SOURCES or ""
    return [url.strip() for url in raw.split(",") if url.strip()]


def source_host(url: str) -> str:
    """Bare host of a source URL, for provenance labelling."""
    host = re.sub(r"^https?://", "", (url or "").strip().lower())
    return host.split("/")[0]


async def fetch_price_list(url: str, timeout: Optional[float] = None) -> str:
    """GET a published price list. Raises on any transport/HTTP error."""
    import httpx

    budget = timeout or settings.ONLINE_PRICE_SEARCH_TIMEOUT_SECONDS
    async with httpx.AsyncClient(
        timeout=budget, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _payload(
    rate: float,
    unit: Optional[str],
    name: str,
    city: str,
    confidence: float,
    *,
    source_name: str = "",
    source_url: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "rate": round(float(rate), 2),
        "unit": normalize_unit(unit) or (unit or ""),
        "product_name": name,
        "product_code": "",
        "price_source": LIVE_SOURCE,
        "source": LIVE_SOURCE,
        "confidence": confidence,
        "city": city,
        "price_basis": LIVE_LABEL,
        "source_name": source_name,
        "source_url": source_url,
        "online_market": True,
        "fetched_at": datetime.utcnow().isoformat(),
    }
    if extra:
        payload.update(extra)
    return payload


async def _from_price_lists(
    description: str, city: str, unit: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Cheapest matching row on any configured published price list."""
    for url in price_list_sources():
        try:
            raw = await fetch_price_list(url)
        except Exception as exc:  # site down / blocked / timed out — try next
            logger.info("Online price list %s unavailable: %s", source_host(url), exc)
            continue

        row = match_price_row(parse_price_rows(raw), description, unit)
        if not row:
            continue

        # A published price list is already a current market figure, so no city
        # multiplier is invented on top of it — the city is recorded instead.
        return _payload(
            row["price"],
            unit,
            row["name"],
            city,
            0.7,
            source_name=source_host(url),
            source_url=url,
            extra={
                # True only when the page itself stated the unit, so a figure the
                # label alone implied is flagged as less certain.
                "unit_confirmed": bool(row.get("unit")),
                "listed_unit": row.get("unit") or None,
            },
        )
    return None


_SEARCH_PROMPT = """You are a Nigerian construction procurement analyst with live web search.

Find the CURRENT best (lowest reliable) price a buyer can pay TODAY for this material.

Material: {description}
City: {city}, Nigeria
Selling unit required: {unit}

Search Nigerian building-material marketplaces and published price lists for the
item and return the best current price you can verify.

Return ONLY valid JSON:
{{
  "rate": number (price in NGN for one {unit}, or null if you cannot verify),
  "unit": "{unit}",
  "product_name": "string",
  "source_name": "string (platform the price came from)",
  "source_url": "string (page the price was seen on)",
  "confidence": number (0-1)
}}

The rate MUST be per {unit}. If you cannot verify a current price in {unit},
return {{"rate": null, "confidence": 0}}."""


async def _from_web_search(
    description: str, city: str, unit: Optional[str]
) -> Optional[Dict[str, Any]]:
    """General web search (Gemini + Google Search) for the current best price."""
    from app.services.gemini_client import get_gemini_client

    client = get_gemini_client()
    prompt = _SEARCH_PROMPT.format(description=description, city=city, unit=unit)
    response = await asyncio.to_thread(
        client.models.generate_content,
        model="gemini-3.6-flash",
        contents=[prompt],
        config={
            "temperature": 0.0,
            "max_output_tokens": 1024,
            "tools": [{"google_search": {}}],
        },
    )
    text = getattr(response, "text", None)
    if not text:
        return None

    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        raw_json = match.group(1)
    else:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        raw_json = match.group(0)

    result = json.loads(raw_json)
    rate = result.get("rate")
    if rate is None or float(rate) <= 0:
        return None

    # Unit gate: the model must confirm it priced the line's selling unit.
    line_unit = normalize_unit(unit)
    if normalize_unit(result.get("unit")) != line_unit:
        logger.info(
            "Online search for '%s' returned unit %r, not %r — rejected",
            description, result.get("unit"), unit,
        )
        return None

    source_url = str(result.get("source_url") or "")
    return _payload(
        float(rate),
        unit,
        str(result.get("product_name") or description),
        city,
        max(0.0, min(1.0, float(result.get("confidence") or 0.3))),
        source_name=str(result.get("source_name") or source_host(source_url)),
        source_url=source_url,
        extra={"unit_confirmed": True},
    )



# ── Cache ───────────────────────────────────────────────────────────────────

# In-process cache: (key -> (stored_at, payload_or_None)). `None` is cached too,
# so one BOQ does not re-attempt a hopeless lookup for every repeated line.
_CACHE: Dict[str, Tuple[datetime, Optional[Dict[str, Any]]]] = {}


def _cache_key(description: str, city: str, unit: Optional[str]) -> str:
    return f"{_normalize(description)}|{_normalize(city)}|{normalize_unit(unit) or ''}"


def _cache_ttl() -> timedelta:
    return timedelta(hours=max(1, int(settings.ONLINE_PRICE_SEARCH_CACHE_HOURS)))


def clear_cache() -> None:
    """Drop the in-process cache (tests and manual refreshes)."""
    _CACHE.clear()


def _cache_get(key: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    entry = _CACHE.get(key)
    if entry is None:
        return False, None
    stored_at, payload = entry
    if datetime.utcnow() - stored_at > _cache_ttl():
        _CACHE.pop(key, None)
        return False, None
    return True, payload


def _cache_set(key: str, payload: Optional[Dict[str, Any]]) -> None:
    _CACHE[key] = (datetime.utcnow(), payload)


async def _mongo_get(mongo_db, key: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    if mongo_db is None:
        return False, None
    try:
        doc = await mongo_db[CACHE_COLLECTION].find_one({"_id": key})
    except Exception as exc:  # cache is best-effort only
        logger.debug("Online price cache read failed: %s", exc)
        return False, None
    if not doc:
        return False, None

    stored_at = doc.get("stored_at")
    if isinstance(stored_at, datetime) and datetime.utcnow() - stored_at > _cache_ttl():
        return False, None
    payload = doc.get("payload")
    return True, payload


async def _mongo_set(mongo_db, key: str, payload: Optional[Dict[str, Any]]) -> None:
    if mongo_db is None:
        return
    try:
        await mongo_db[CACHE_COLLECTION].replace_one(
            {"_id": key},
            {
                "_id": key,
                "payload": payload,
                "stored_at": datetime.utcnow(),
                "source": LIVE_SOURCE,
            },
            upsert=True,
        )
    except Exception as exc:  # never let caching break a BOQ
        logger.debug("Online price cache write failed: %s", exc)


# ── Public API ──────────────────────────────────────────────────────────────


async def search_online_price(
    description: str,
    city: str = "Abuja",
    unit: Optional[str] = None,
    mongo_db=None,
) -> Optional[Dict[str, Any]]:
    """Current best online price for a line item, or None.

    Called ONLY after the product catalogue and material_rates missed. Returns
    None for any failure or when the figure cannot be tied to the line's selling
    unit — the caller then continues down the chain, so a BOQ always totals.
    """
    if not settings.ONLINE_PRICE_SEARCH_ENABLED:
        return None
    if not _normalize(description) or not normalize_unit(unit):
        # Without a selling unit there is no way to tell a per-bag figure from a
        # per-m2 one, and a contract rate must never be guessed.
        return None

    key = _cache_key(description, city, unit)
    hit, cached = _cache_get(key)
    if not hit:
        mongo_hit, mongo_cached = await _mongo_get(mongo_db, key)
        if mongo_hit:
            _cache_set(key, mongo_cached)
            return mongo_cached

    if hit:
        return cached

    result: Optional[Dict[str, Any]] = None
    try:
        result = await _from_price_lists(description, city, unit)
    except Exception as exc:
        logger.info("Online price list lookup failed for '%s': %s", description, exc)

    if result is None:
        try:
            result = await _from_web_search(description, city, unit)
        except Exception as exc:
            logger.info("Online web search failed for '%s': %s", description, exc)

    if result is not None:
        logger.info(
            "Online market price for '%s': NGN %s per %s (from %s)",
            description, result["rate"], unit, result.get("source_name") or "web search",
        )

    _cache_set(key, result)
    await _mongo_set(mongo_db, key, result)
    return result
