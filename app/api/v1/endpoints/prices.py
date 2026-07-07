from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional

from app.core.database import get_db
from app.models.product import Product

import logging

logger = logging.getLogger(__name__)

router = APIRouter()


### Get live market prices for products
@router.get("/")
async def get_live_prices(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """
    Get current market prices for products.
    Returns price data with trend information.
    """
    query = select(Product).where(Product.status == "active")

    if search:
        query = query.where(Product.name.ilike(f"%{search}%"))

    query = query.order_by(Product.sales_count.desc()).limit(limit)

    result = await db.execute(query)
    products = result.scalars().all()

    prices = []
    for p in products:
        current_price = float(p.discount_price or p.base_price)
        base_price = float(p.base_price)

        # Determine trend based on discount
        if p.discount_price and p.discount_price < p.base_price:
            trend = "down"
            change = f"-{p.discount_percentage}%" if p.discount_percentage else f"-{round((1 - current_price / base_price) * 100)}%"
        elif p.discount_price and p.discount_price > p.base_price:
            trend = "up"
            change = f"+{round((current_price / base_price - 1) * 100)}%"
        else:
            trend = "stable"
            change = None

        prices.append({
            "name": p.name,
            "region": "Abuja region",
            "current_price": current_price,
            "unit": p.unit_of_measure or "unit",
            "trend": trend,
            "change": change,
        })

    return {"prices": prices}
