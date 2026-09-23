from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, case
from sqlalchemy.orm import selectinload
from typing import Optional
from uuid import UUID
from datetime import datetime, timedelta

from app.core.database import get_db
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.vendor import Vendor
from app.api.deps import get_current_user, get_current_vendor

import logging
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


### Dashboard stats for the current user
@router.get("/stats")
async def get_dashboard_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id

    # Count active orders
    active_result = await db.execute(
        select(func.count(Order.id))
        .where(
            Order.user_id == user_id,
            Order.status.notin_(["delivered", "cancelled", "refunded"])
        )
    )
    active_orders = active_result.scalar() or 0

    # Count vetted suppliers (vendors with verification_status = 'verified')
    vendor_result = await db.execute(
        select(func.count(Vendor.id)).where(Vendor.verification_status == "verified")
    )
    vetted_suppliers = vendor_result.scalar() or 0

    # Average delivery days (from completed orders)
    delivery_result = await db.execute(
        select(func.avg(
            func.extract('epoch', Order.delivered_at - Order.created_at) / 86400
        )).where(
            Order.status == "delivered",
            Order.delivered_at.isnot(None)
        )
    )
    avg_delivery_days = round(float(delivery_result.scalar() or 0), 1)

    # Total verified savings (sum of discount_amount from delivered orders only)
    savings_result = await db.execute(
        select(func.sum(Order.discount_amount))
        .where(
            Order.user_id == user_id,
            Order.status == "delivered"
        )
    )
    total_savings = float(savings_result.scalar() or 0)

    # Fallback: for delivered orders where discount_amount is 0 (legacy orders),
    # calculate savings from OrderItem.unit_price vs Product.base_price
    if total_savings == 0:
        fallback_result = await db.execute(
            select(func.sum(
                (Product.base_price - OrderItem.unit_price) * OrderItem.quantity
            ))
            .select_from(OrderItem)
            .join(Order, OrderItem.order_id == Order.id)
            .join(Product, OrderItem.product_id == Product.id)
            .where(
                Order.user_id == user_id,
                Order.status == "delivered",
                Product.discount_price.isnot(None)
            )
        )
        total_savings = float(fallback_result.scalar() or 0)

    # Potential savings from items currently in the user's cart
    # Compare cart item prices vs market prices
    from app.models.cart import CartItem as CartItemModel
    potential_savings = 0.0
    cart_result = await db.execute(
        select(CartItemModel)
        .where(CartItemModel.user_id == user_id)
    )
    cart_items = cart_result.scalars().all()
    if cart_items:
        for ci in cart_items:
            prod_result = await db.execute(
                select(Product).where(Product.id == ci.product_id)
            )
            prod = prod_result.scalar_one_or_none()
            if prod and prod.discount_price and float(prod.base_price) > float(prod.discount_price):
                potential_savings += (float(prod.base_price) - float(prod.discount_price)) * ci.quantity

    # Calculate savings trend (compare current period vs previous period)
    # For now, calculate based on available data
    savings_trend = 0.0
    delivery_trend = 0.0

    # If we have savings data, calculate a meaningful trend
    if total_savings > 0:
        # Get previous period savings (last 30 days before the earliest current order)
        from datetime import timedelta
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        prev_savings_result = await db.execute(
            select(func.sum(Order.discount_amount))
            .where(
                Order.user_id == user_id,
                Order.created_at < thirty_days_ago
            )
        )
        prev_savings = float(prev_savings_result.scalar() or 0)
        if prev_savings > 0:
            savings_trend = round(((total_savings - prev_savings) / prev_savings) * 100, 1)

    # Calculate delivery trend
    if avg_delivery_days > 0:
        # Get previous period average delivery days
        from datetime import timedelta
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        prev_delivery_result = await db.execute(
            select(func.avg(
                func.extract('epoch', Order.delivered_at - Order.created_at) / 86400
            )).where(
                Order.status == "delivered",
                Order.delivered_at.isnot(None),
                Order.created_at < thirty_days_ago
            )
        )
        prev_avg = float(prev_delivery_result.scalar() or 0)
        if prev_avg > 0:
            delivery_trend = round(((avg_delivery_days - prev_avg) / prev_avg) * 100, 1)

    return {
        "total_savings": total_savings,
        "potential_savings": potential_savings,
        "active_orders": active_orders,
        "vetted_suppliers": vetted_suppliers,
        "avg_delivery_days": avg_delivery_days,
        "savings_trend": savings_trend,
        "delivery_trend": delivery_trend,
    }


### Reporting windows: a preset period OR an explicit [start_date, end_date] range
_PRESET_DAYS = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}
_MAX_RANGE_DAYS = 366
# Money still in flight (accepted but not delivered) and money lost.
_IN_FLIGHT_STATUSES = ("pending_payment", "confirmed", "processing", "ready_for_pickup")
_LOST_STATUSES = ("cancelled", "refunded")


def _period_days(period: str) -> int:
    return _PRESET_DAYS.get(period, 30)


def _resolve_window(period: str, start_date: Optional[str], end_date: Optional[str]):
    """Resolve a reporting window to (start, end) UTC datetimes.

    Accepts either a preset `period` or an explicit ISO date range; the two range
    endpoints must be supplied together. The end date is inclusive. Rejects
    inverted or oversized ranges and clamps the window to now.
    """
    now = datetime.utcnow()
    if start_date or end_date:
        if not (start_date and end_date):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="start_date and end_date must be provided together",
            )
        try:
            start = datetime.strptime((start_date or "").strip(), "%Y-%m-%d")
            # Inclusive end date -> run to the start of the following day.
            end = datetime.strptime((end_date or "").strip(), "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="start_date and end_date must be YYYY-MM-DD",
            )
        if end <= start:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="end_date must be on or after start_date",
            )
        if (end - start).days > _MAX_RANGE_DAYS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Range cannot exceed {_MAX_RANGE_DAYS} days",
            )
        return start, min(end, now)

    days = _period_days((period or "30d").strip().lower())
    return now - timedelta(days=days), now


def _range_filters(vendor_id, start: datetime, end: datetime):
    """Shared vendor + window predicates (half-open [start, end))."""
    return (
        OrderItem.vendor_id == vendor_id,
        Order.created_at >= start,
        Order.created_at < end,
    )


async def _window_metrics(db: AsyncSession, vendor_id, start: datetime, end: datetime) -> dict:
    """Delivered revenue/orders (+ in-flight and lost counts) for one window.

    Revenue, order count and AOV are all derived from the SAME population
    (delivered orders), so they can never disagree. The previous implementation
    divided delivered revenue by a count that included every status, which
    under-reported AOV and inflated the order count.
    """
    common = _range_filters(vendor_id, start, end)

    delivered_row = (await db.execute(
        select(
            func.coalesce(func.sum(OrderItem.total_price), 0),
            func.count(Order.id.distinct()),
        )
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(*common, Order.status == "delivered")
    )).one()
    revenue = float(delivered_row[0] or 0)
    orders = int(delivered_row[1] or 0)

    others_row = (await db.execute(
        select(
            func.count(func.distinct(case((Order.status.in_(_IN_FLIGHT_STATUSES), Order.id)))),
            func.count(func.distinct(case((Order.status.in_(_LOST_STATUSES), Order.id)))),
        )
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(*common)
    )).one()

    return {
        "revenue": revenue,
        "orders": orders,
        "pending": int(others_row[0] or 0),
        "cancelled": int(others_row[1] or 0),
        "average_order_value": _aov(revenue, orders),
    }


def _change(curr: float, prev: float) -> dict:
    """Period-over-period change as a numeric value plus a display string.

    A window with no baseline reports "New" instead of a misleading "+100%".
    """
    if prev == 0:
        if curr == 0:
            return {"value": 0.0, "display": "0%"}
        return {"value": None, "display": "New"}
    pct = ((curr - prev) / prev) * 100
    return {"value": round(pct, 1), "display": f"{'+' if pct >= 0 else ''}{pct:.1f}%"}


def _aov(revenue: float, orders: int) -> float:
    """Average order value — revenue and orders must share one population."""
    return round(revenue / orders, 2) if orders else 0


def _bucket_start(ts: datetime, granularity: str) -> datetime:
    """Start of the day (or ISO week — Monday) containing `ts`."""
    if granularity == "week":
        return (ts - timedelta(days=ts.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


async def _revenue_series(db: AsyncSession, vendor_id, start: datetime, end: datetime, granularity: str) -> list:
    """Delivered revenue per bucket, gap-filled so the chart has no holes."""
    bucket = func.date_trunc(granularity, Order.created_at)
    rows = (await db.execute(
        select(
            bucket.label("bucket"),
            func.coalesce(func.sum(OrderItem.total_price), 0).label("revenue"),
            func.count(Order.id.distinct()).label("orders"),
        )
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(*_range_filters(vendor_id, start, end), Order.status == "delivered")
        .group_by(bucket)
        .order_by(bucket)
    )).all()

    found = {
        _bucket_start(r.bucket, granularity): (float(r.revenue or 0), int(r.orders or 0))
        for r in rows if r.bucket
    }

    step = timedelta(days=7 if granularity == "week" else 1)
    cursor = _bucket_start(start, granularity)
    last = _bucket_start(end, granularity)
    series = []
    while cursor <= last:
        revenue, orders = found.get(cursor, (0.0, 0))
        series.append({"date": cursor.date().isoformat(), "revenue": revenue, "orders": orders})
        cursor += step
    return series


### Sales analytics (vendor-facing)
@router.get("/sales")
async def get_sales_analytics(
    period: str = Query("30d"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    """Sales analytics for a preset period or a custom [start_date, end_date] range.

    Revenue, delivered-order count and AOV all come from the delivered population;
    in-flight and cancelled orders are reported as separate counts. `series` is
    bucketed daily (<=92 day windows) or weekly so long ranges stay responsive.
    """
    vendor_id = UUID(current_vendor["id"])
    start, end = _resolve_window(period, start_date, end_date)

    metrics = await _window_metrics(db, vendor_id, start, end)
    span_days = max(1, (end - start).days)
    granularity = "day" if span_days <= 92 else "week"
    series = await _revenue_series(db, vendor_id, start, end, granularity)

    return {
        "period": period,
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "granularity": granularity,
        "total_revenue": metrics["revenue"],
        "total_orders": metrics["orders"],
        "average_order_value": metrics["average_order_value"],
        "pending_orders": metrics["pending"],
        "cancelled_orders": metrics["cancelled"],
        "series": series,
    }


### Sales comparison (current window vs the preceding window of equal length)
@router.get("/sales/compare")
async def get_sales_comparison(
    period: str = Query("30d"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    """Compare the selected window against the immediately preceding one.

    Both windows use the same delivered-based definitions as /sales. Percentage
    changes are returned as display strings (backwards compatible) plus numeric
    values; a window with no baseline reports "New" rather than a misleading
    "+100%".
    """
    vendor_id = UUID(current_vendor["id"])
    start, end = _resolve_window(period, start_date, end_date)
    length = end - start
    prev_start, prev_end = start - length, start

    current = await _window_metrics(db, vendor_id, start, end)
    previous = await _window_metrics(db, vendor_id, prev_start, prev_end)

    revenue_change = _change(current["revenue"], previous["revenue"])
    orders_change = _change(current["orders"], previous["orders"])
    pending_change = _change(current["pending"], previous["pending"])
    aov_change = _change(current["average_order_value"], previous["average_order_value"])

    return {
        "period": period,
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "previous_start_date": prev_start.date().isoformat(),
        "previous_end_date": prev_end.date().isoformat(),
        "current": current,
        "previous": previous,
        "revenue_change": revenue_change["display"],
        "orders_change": orders_change["display"],
        "pending_change": pending_change["display"],
        "average_order_value_change": aov_change["display"],
        "revenue_change_pct": revenue_change["value"],
        "orders_change_pct": orders_change["value"],
        "pending_change_pct": pending_change["value"],
        "average_order_value_change_pct": aov_change["value"],
    }


### On-time delivery rate for vendor
@router.get("/on-time-rate")
async def get_on_time_delivery_rate(
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    vendor_id = UUID(current_vendor["id"])

    # Total delivered orders for this vendor
    total_result = await db.execute(
        select(func.count(Order.id.distinct()))
        .select_from(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            OrderItem.vendor_id == vendor_id,
            Order.status == "delivered",
        )
    )
    total_delivered = total_result.scalar() or 0

    # On-time delivered (delivered_at <= estimated_delivery_date)
    on_time_result = await db.execute(
        select(func.count(Order.id.distinct()))
        .select_from(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            OrderItem.vendor_id == vendor_id,
            Order.status == "delivered",
            Order.delivered_at.isnot(None),
            Order.estimated_delivery_date.isnot(None),
            Order.delivered_at <= Order.estimated_delivery_date,
        )
    )
    on_time = on_time_result.scalar() or 0

    rate = round((on_time / total_delivered) * 100, 1) if total_delivered > 0 else 0

    return {
        "on_time_rate": rate,
        "total_delivered": total_delivered,
        "on_time_delivered": on_time,
    }


### Top selling products (vendor-scoped)
@router.get("/top-products")
async def get_top_products(
    limit: int = Query(10, ge=1, le=50),
    period: str = Query("30d"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    """The vendor's OWN products, ranked by delivered revenue in the window.

    This used to be a global `select(Product).order_by(sales_count)`, so every
    vendor was shown the whole marketplace's best sellers. It now joins delivered
    order items for this vendor only, falling back to lifetime `sales_count` so a
    vendor with no sales yet still sees their catalogue.
    """
    vendor_id = UUID(current_vendor["id"])
    start, end = _resolve_window(period, start_date, end_date)

    sold = (
        select(
            OrderItem.product_id.label("product_id"),
            func.coalesce(func.sum(OrderItem.total_price), 0).label("revenue"),
            func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
        )
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(*_range_filters(vendor_id, start, end), Order.status == "delivered")
        .group_by(OrderItem.product_id)
        .subquery()
    )

    rows = (await db.execute(
        select(
            Product,
            func.coalesce(sold.c.revenue, 0).label("revenue"),
            func.coalesce(sold.c.units, 0).label("units"),
        )
        .options(selectinload(Product.brand))
        .outerjoin(sold, sold.c.product_id == Product.id)
        .where(Product.vendor_id == vendor_id)
        .order_by(desc(func.coalesce(sold.c.revenue, 0)), desc(Product.sales_count))
        .limit(limit)
    )).all()

    return [
        {
            "id": str(p.id),
            "name": p.name,
            "category": p.brand.name if p.brand else None,
            # `sales` = units sold in the selected window; `lifetime_sales` = all-time.
            "sales": int(units or 0),
            "lifetime_sales": int(p.sales_count or 0),
            "revenue": float(revenue or 0),
            "price": round(float(revenue) / int(units), 2) if units else float(p.discount_price or p.base_price or 0),
        }
        for p, revenue, units in rows
    ]


### Savings trend (monthly savings for the current user)
@router.get("/savings-trend")
async def get_savings_trend(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id

    # Aggregate discount_amount by month for this user's orders
    month_col = func.date_trunc('month', Order.created_at)
    result = await db.execute(
        select(
            month_col.label('month'),
            func.sum(Order.discount_amount).label('savings'),
        )
        .select_from(Order)
        .where(
            Order.user_id == user_id,
            Order.discount_amount > 0,
        )
        .group_by(month_col)
        .order_by(month_col)
    )
    rows = result.all()

    trend = [
        {
            "month": row.month.strftime("%Y-%m") if row.month else "N/A",
            "savings": float(row.savings or 0),
        }
        for row in rows
    ]

    return {"trend": trend}


### Vendor dashboard stats (for SupplierAdmin.tsx)
@router.get("/vendor-stats")
async def get_vendor_stats(
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    vendor_id = UUID(current_vendor["id"])

    # Total sales (sum of order item totals for this vendor)
    sales_result = await db.execute(
        select(func.sum(OrderItem.total_price))
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            OrderItem.vendor_id == vendor_id,
            Order.status == "delivered"
        )
    )
    total_sales = float(sales_result.scalar() or 0)

    # Active orders (processing or shipped)
    active_result = await db.execute(
        select(func.count(Order.id.distinct()))
        .select_from(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            OrderItem.vendor_id == vendor_id,
            Order.status.in_(["processing", "shipped", "in_transit"])
        )
    )
    active_orders = active_result.scalar() or 0

    # Pending orders (pending or confirmed)
    pending_result = await db.execute(
        select(func.count(Order.id.distinct()))
        .select_from(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            OrderItem.vendor_id == vendor_id,
            Order.status.in_(["pending_payment", "pending", "confirmed"])
        )
    )
    pending_orders = pending_result.scalar() or 0

    # Completed orders (delivered)
    completed_result = await db.execute(
        select(func.count(Order.id.distinct()))
        .select_from(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            OrderItem.vendor_id == vendor_id,
            Order.status == "delivered"
        )
    )
    completed_orders = completed_result.scalar() or 0

    return {
        "totalSales": total_sales,
        "activeOrders": active_orders,
        "pendingOrders": pending_orders,
        "completedOrders": completed_orders,
    }
