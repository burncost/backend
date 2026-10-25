"""Natural-key + category helpers for persisting observed online rates.

The BOQ pipeline can price a line from an online listing when neither our
catalogue nor `material_rates` has a rate. Caching that observation back into
`material_rates` is only safe if it is (a) idempotent and (b) clearly marked as
provisional. This module owns the key derivation and the lazy "Online Market"
category fallback; `online_rate_writeback` owns the transactional upsert.

Kept separate from `price_service` so importing it never pulls the whole price
stack (and to keep the key format in one testable place).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.material_rate import MaterialRate

ONLINE_CATEGORY_NAME = "Online Market"
ONLINE_CATEGORY_SLUG = "online-market"

# Rates the write-back path is allowed to persist. `ai_estimate` is deliberately
# absent: a model guess must never be stored as a market observation.
PERSISTABLE_SOURCES = frozenset({"online_market", "online_average"})


def normalize_product_name(name: str) -> str:
    """Lowercase, strip listing noise and collapse whitespace for keying.

    Only used for the natural key - `material_name` keeps the original wording.
    """
    text = (name or "").lower().replace("\u00a0", " ")
    # Drop the provenance suffix the online tiers add ("...(online average)").
    text = re.sub(r"\(online(?: average)?\)", " ", text)
    text = re.sub(r"\b(?:online|average)\b", " ", text)
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_unit(unit: Optional[str]) -> str:
    """Canonical unit spelling, so 'Bags' and 'bag' share a cache row."""
    text = (unit or "").strip().lower().rstrip(".")
    text = text.replace("\u00b2", "2").replace("\u00b3", "3")
    aliases = {
        "m": "m", "metre": "m", "meter": "m", "lm": "m",
        "sqm": "m2", "sq.m": "m2", "sq m": "m2", "square metre": "m2", "square meter": "m2",
        "nos": "nr", "no": "nr", "pc": "nr", "pcs": "nr", "piece": "nr", "pieces": "nr",
        "ton": "tonne", "tons": "tonne", "t": "tonne",
        "bags": "bag", "blocks": "block", "bundles": "bundle",
    }
    return aliases.get(text, text)


def state_for_city(city: Optional[str]) -> str:
    """Map a colloquial city name onto the STATE value `material_rates` stores."""
    from app.services.price_service import CITY_TO_STATE  # local: avoid import cycle

    key = (city or "").strip().lower()
    return CITY_TO_STATE.get(key, (city or "FCT").strip() or "FCT")


def rate_lookup_key(product_name: str, unit: Optional[str], state: Optional[str]) -> str:
    """sha1(normalize(name)|unit|state) - stable natural key for cached rates."""
    raw = "|".join([
        normalize_product_name(product_name),
        normalize_unit(unit),
        (state or "").strip().lower(),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


async def get_or_create_online_category(session: AsyncSession) -> Category:
    """Return the house category for web-observed rates, creating it lazily.

    A rate cannot be stored without a category (`category_id` is NOT NULL), and
    forcing an online observation into a curated category would misrepresent it.
    """
    row = (await session.execute(
        select(Category).where(Category.slug == ONLINE_CATEGORY_SLUG)
    )).scalar_one_or_none()
    if row:
        return row

    category = Category(
        name=ONLINE_CATEGORY_NAME,
        slug=ONLINE_CATEGORY_SLUG,
        description=(
            "Rates observed on public online listings. Indicative only - confirm "
            "with a supplier before relying on them."
        ),
        division="Reference",
        material_type="material",
        is_active=True,
    )
    session.add(category)
    await session.flush()
    return category


async def find_cached_rate(
    session: AsyncSession, lookup_key: str
) -> Optional[MaterialRate]:
    return (await session.execute(
        select(MaterialRate).where(MaterialRate.lookup_key == lookup_key)
    )).scalar_one_or_none()


def utcnow() -> datetime:
    return datetime.utcnow()
