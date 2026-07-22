from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
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

logger = logging.getLogger(__name__)

router = APIRouter()


### Dashboard stats for the current user
@router.get("/stats")
async def get_dashboard_stats(
    current_user: dict = Depends(get_current_user),
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


### Helper: parse period into timedelta
def _period_days(period: str) -> int:
    return {"7d": 7, "30d": 30, "90d": 90, "1y": 365}.get(period, 30)


### Sales analytics (vendor-facing)
@router.get("/sales")
async def get_sales_analytics(
    period: str = Query("30d", regex="^(7d|30d|90d|1y)$"),
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    vendor_id = UUID(current_vendor["id"])
    since = datetime.utcnow() - timedelta(days=_period_days(period))

    # Revenue from delivered orders for this vendor
    revenue_result = await db.execute(
        select(func.sum(OrderItem.total_price))
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            OrderItem.vendor_id == vendor_id,
            Order.status == "delivered",
            Order.created_at >= since,
        )
    )
    total_revenue = float(revenue_result.scalar() or 0)

    # Total orders (all statuses) for this vendor in period
    orders_result = await db.execute(
        select(func.count(Order.id.distinct()))
        .select_from(Order)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            OrderItem.vendor_id == vendor_id,
            Order.created_at >= since,
        )
    )
    total_orders = orders_result.scalar() or 0

    return {
        "period": period,
        "total_revenue": total_revenue,
        "total_orders": total_orders,
        "average_order_value": round(total_revenue / total_orders, 2) if total_orders > 0 else 0,
    }


### Sales comparison (current period vs previous period)
@router.get("/sales/compare")
async def get_sales_comparison(
    period: str = Query("30d", regex="^(7d|30d|90d|1y)$"),
    current_vendor: dict = Depends(get_current_vendor),
    db: AsyncSession = Depends(get_db)
):
    vendor_id = UUID(current_vendor["id"])
    days = _period_days(period)
    now = datetime.utcnow()

    # Current period
    current_start = now - timedelta(days=days)
    # Previous period (same length, before current)
    prev_start = current_start - timedelta(days=days)

    async def _period_stats(since: datetime, until: datetime) -> dict:
        rev_result = await db.execute(
            select(func.sum(OrderItem.total_price))
            .select_from(OrderItem)
            .join(Order, OrderItem.order_id == Order.id)
            .where(
                OrderItem.vendor_id == vendor_id,
                Order.status == "delivered",
                Order.created_at >= since,
                Order.created_at < until,
            )
        )
        revenue = float(rev_result.scalar() or 0)

        ord_result = await db.execute(
            select(func.count(Order.id.distinct()))
            .select_from(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                OrderItem.vendor_id == vendor_id,
                Order.created_at >= since,
                Order.created_at < until,
            )
        )
        total_orders = ord_result.scalar() or 0

        pend_result = await db.execute(
            select(func.count(Order.id.distinct()))
            .select_from(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                OrderItem.vendor_id == vendor_id,
                Order.status.in_(["pending_payment", "pending", "confirmed"]),
                Order.created_at >= since,
                Order.created_at < until,
            )
        )
        pending = pend_result.scalar() or 0

        return {"revenue": revenue, "orders": total_orders, "pending": pending}

    current = await _period_stats(current_start, now)
    previous = await _period_stats(prev_start, current_start)

    def pct_change(curr: float, prev: float) -> str:
        if prev == 0:
            return "+100%" if curr > 0 else "0%"
        change = ((curr - prev) / prev) * 100
        return f"{'+' if change >= 0 else ''}{change:.1f}%"

    return {
        "period": period,
        "current": current,
        "previous": previous,
        "revenue_change": pct_change(current["revenue"], previous["revenue"]),
        "orders_change": pct_change(current["orders"], previous["orders"]),
        "pending_change": pct_change(current["pending"], previous["pending"]),
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


### Top selling products
@router.get("/top-products")
async def get_top_products(
    limit: int = Query(10, ge=1, le=50),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.brand))
        .order_by(desc(Product.sales_count))
        .limit(limit)
    )
    products = result.scalars().all()

    return [
        {
            "id": str(p.id),
            "name": p.name,
            "category": p.brand.name if p.brand else None,
            "sales": p.sales_count,
            "revenue": float(p.sales_count * (p.discount_price or p.base_price)),
            "price": float(p.discount_price or p.base_price),
        }
        for p in products
    ]


### Savings trend (monthly savings for the current user)
@router.get("/savings-trend")
async def get_savings_trend(
    current_user: dict = Depends(get_current_user),
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
