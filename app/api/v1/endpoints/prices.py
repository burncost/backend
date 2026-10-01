from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import date

from app.core.database import get_db
from app.models.product import Product

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _location_tokens(value: Optional[str]) -> set:
    """Split a free-text location ("Abuja", "Lekki, Lagos") into lowercase tokens."""
    if not value:
        return set()
    cleaned = value.lower().replace(",", " ").replace("/", " ")
    return {tok for tok in cleaned.split() if len(tok) >= 3}


def _is_near(user_tokens: set, region: str) -> bool:
    """True when the vendor's region overlaps the shopper's location tokens."""
    if not user_tokens or not region:
        return False
    region_l = region.lower()
    return any(tok in region_l for tok in user_tokens)


### Get live market prices for products
@router.get("/")
async def get_live_prices(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """
    Get current market prices for products.

    Prices are refreshed once a day (not hourly). When a `location` is supplied
    the nearest suppliers (city/state match) are listed first so the verification
    reflects the shopper's own market; the caller falls back to the national
    ordering when the location is unknown.
    """
    query = (
        select(Product)
        .where(Product.status == "active")
        .options(selectinload(Product.vendor))
    )

    if search:
        query = query.where(Product.name.ilike(f"%{search}%"))

    query = query.order_by(Product.sales_count.desc()).limit(limit)

    result = await db.execute(query)
    products = result.scalars().all()

    user_tokens = _location_tokens(location)

    prices = []
    for p in products:
        current_price = float(p.discount_price or p.base_price)
        base_price = float(p.base_price)

        vendor = getattr(p, "vendor", None)
        region_parts = [getattr(vendor, "city", None), getattr(vendor, "state", None)] if vendor else []
        region = ", ".join([part for part in region_parts if part]) or "Nigeria"

        # Determine trend based on discount. The percentage is always derived from
        # the actual prices: the stored discount_percentage column can be stale and
        # then disagrees with the price fields rendered next to it in the UI.
        if p.discount_price and p.discount_price < p.base_price:
            trend = "down"
            change = f"-{round((1 - current_price / base_price) * 100, 2):g}%"
        elif p.discount_price and p.discount_price > p.base_price:
            trend = "up"
            change = f"+{round((current_price / base_price - 1) * 100, 2):g}%"
        else:
            trend = "stable"
            change = None

        prices.append({
            "name": p.name,
            "region": region,
            "current_price": current_price,
            "unit": p.unit_of_measure or "unit",
            "trend": trend,
            "change": change,
            "location_match": _is_near(user_tokens, region),
        })

    # Nearest-market first: stable sort keeps the sales ordering within each group.
    if user_tokens:
        prices.sort(key=lambda item: not item["location_match"])

    return {
        "prices": prices,
        "user_location": location,
        "location_applied": bool(user_tokens),
        "last_updated": date.today().isoformat(),
        "update_frequency": "daily",
    }
