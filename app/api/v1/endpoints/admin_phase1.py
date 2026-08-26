"""Phase 1 admin endpoints — dashboard charts, activity feed, notifications,
order detail, risk entities/trends, analytics overview. Uses existing models only.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from uuid import UUID

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.user import User, UserProfile, UserRole
from app.models.vendor import Vendor
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.category import Category
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.services.risk_service import risk_from_vendor

router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")


async def _month_bucket(db, day_span, role=None, model_cls=None):
    """Return monthly counts for the last 6 months."""
    result = []
    now = datetime.utcnow()
    for i in range(5, -1, -1):
        start = (now - timedelta(days=i * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (now - timedelta(days=(i - 1) * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        q = select(func.count(model_cls.id)).where(model_cls.created_at >= start, model_cls.created_at < end)
        if role and hasattr(model_cls, "role"):
            q = q.where(model_cls.role == role)
        count = (await db.execute(q)).scalar() or 0
        result.append({"label": start.strftime("%b"), "count": count})
    return result


@router.get("/stats/charts")
async def admin_stats_charts(current_user: dict = Depends(admin_guard), db: AsyncSession = Depends(get_db)):
    """Dashboard charts: marketplace growth, transaction volume, material demand."""
    now = datetime.utcnow()

    # Marketplace growth (builders + suppliers counts per month)
    growth = []
    for i in range(5, -1, -1):
        start = (now - timedelta(days=i * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (now - timedelta(days=(i - 1) * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        builders = (await db.execute(select(func.count(User.id)).where(User.role == "customer", User.created_at >= start, User.created_at < end))).scalar() or 0
        suppliers = (await db.execute(select(func.count(Vendor.id)).where(Vendor.created_at >= start, Vendor.created_at < end))).scalar() or 0
        growth.append({"month": start.strftime("%b"), "builders": builders, "suppliers": suppliers})

    # Daily transaction volume (last 7 days, ₦m)
    days_volume = []
    for i in range(6, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        amount = (await db.execute(select(func.coalesce(func.sum(Order.total_amount), 0)).where(Order.created_at >= day_start, Order.created_at < day_end))).scalar() or 0
        days_volume.append({"day": day_start.strftime("%a"), "amount": round(float(amount) / 1_000_000, 1)})

    # Material demand by category
    rows = (await db.execute(select(Category.name, func.count(OrderItem.id)).select_from(OrderItem)
                             .join(Product, OrderItem.product_id == Product.id)
                             .join(Category, Product.category_id == Category.id)
                             .group_by(Category.name).order_by(func.count(OrderItem.id).desc()).limit(6))).all()
    material_demand = [{"category": r[0], "orders": r[1]} for r in rows]

    return {"marketplace_growth": growth, "transaction_volume": days_volume, "material_demand": material_demand}


def _activity_meta(entry):
    action = (entry.action or "").lower()
    if any(k in action for k in ("vendor", "supplier", "verify", "approve")):
        return "supplier", "Supplier Updated", "#F5881F"
    if any(k in action for k in ("payment", "escrow", "release", "refund")):
        return "escrow", "Payment Activity", "#F5881F"
    if "order" in action:
        return "order", "Order Activity", "#1A3C6B"
    if any(k in action for k in ("boq", "analysis")):
        return "boq", "BOQ Analysis", "#1E7E34"
    if any(k in action for k in ("fraud", "risk", "alert")):
        return "alert", "Fraud / Risk Alert", "#D32F2F"
    return "system", "System Activity", "#555555"


def _relative(ts, now):
    if not ts:
        return ""
    delta = now - ts if now >= ts else ts - now
    s = int(delta.total_seconds())
    if s < 60:
        return f"{max(1, s)} second{'s' if s != 1 else ''} ago"
    m = s // 60
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h = m // 60
    if h < 24:
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = h // 24
    return f"{d} day{'s' if d != 1 else ''} ago"


@router.get("/stats/recent-activity")
async def admin_stats_recent_activity(limit: int = Query(8, ge=1, le=50),
                                      current_user: dict = Depends(admin_guard),
                                      db: AsyncSession = Depends(get_db)):
    """Recent platform activity (audit logs) for the dashboard feed."""
    result = await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
    entries = result.scalars().all()
    now = datetime.utcnow()
    return {"activities": [
        {"id": str(e.id), "type": _activity_meta(e)[0], "title": _activity_meta(e)[1],
         "description": f"{_activity_meta(e)[1]} — {e.resource_type or 'system'} ({e.resource_id or e.action})",
         "time": _relative(e.created_at, now), "color": _activity_meta(e)[2]}
        for e in entries
    ]}


@router.get("/orders/{order_id}/detail")
async def admin_order_detail(order_id: UUID, current_user: dict = Depends(admin_guard),
                             db: AsyncSession = Depends(get_db)):
    """Full order detail (buyer, supplier, items) for Escrow view."""
    order = (await db.execute(select(Order)
              .options(selectinload(Order.user).selectinload(User.profile),
                       selectinload(Order.items).selectinload(OrderItem.vendor),
                       selectinload(Order.shipping_address))
              .where(Order.id == order_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")

    buyer_name = None
    if order.user and order.user.profile:
        buyer_name = f"{order.user.profile.first_name} {order.user.profile.last_name}".strip()
    first_vendor = order.items[0].vendor if order.items else None
    supplier = {}
    if first_vendor:
        supplier = {"business_name": first_vendor.business_name,
                    "contact": (f"{first_vendor.user.profile.first_name} {first_vendor.user.profile.last_name}".strip()
                                if first_vendor.user and first_vendor.user.profile else None)}
    return {
        "id": str(order.id),
        "order_number": order.order_number,
        "status": order.status.value if hasattr(order.status, "value") else str(order.status),
        "payment_status": order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status),
        "payment_method": order.payment_method.value if hasattr(order.payment_method, "value") else None,
        "subtotal": float(order.subtotal), "shipping_fee": float(order.shipping_fee or 0),
        "tax_amount": float(order.tax_amount or 0), "discount_amount": float(order.discount_amount or 0),
        "total_amount": float(order.total_amount),
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "estimated_delivery_date": order.estimated_delivery_date.isoformat() if order.estimated_delivery_date else None,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "shipping_address": {"address_line1": order.shipping_address.address_line1 if order.shipping_address else None,
                             "city": order.shipping_address.city if order.shipping_address else None,
                             "state": order.shipping_address.state if order.shipping_address else None},
        "buyer": {"name": buyer_name, "email": order.user.email if order.user else None,
                  "phone": order.user.phone_number if order.user else None},
        "supplier": supplier,
        "items": [{"id": str(i.id), "product_name": i.product_name, "sku": i.sku, "quantity": i.quantity,
                   "unit_price": float(i.unit_price), "total_price": float(i.total_price),
                   "vendor_status": i.vendor_status.value if hasattr(i.vendor_status, "value") else str(i.vendor_status)}
                  for i in order.items],
    }


@router.get("/notifications")
async def admin_list_notifications(limit: int = Query(50, ge=1, le=200),
                                   current_user: dict = Depends(admin_guard),
                                   db: AsyncSession = Depends(get_db)):
    """Admin-facing notifications stream (all platform notifications)."""
    result = await db.execute(select(Notification).order_by(Notification.created_at.desc()).limit(limit))
    notifications = result.scalars().all()
    unread = sum(1 for n in notifications if not n.read)
    high_priority = sum(1 for n in notifications if n.type == "alert")
    return {
        "notifications": [{"id": str(n.id), "type": n.type or "system", "title": n.title,
                           "message": n.message, "read": n.read,
                           "created_at": n.created_at.isoformat() if n.created_at else None}
                          for n in notifications],
        "unread_count": unread, "high_priority_count": high_priority, "total": len(notifications),
    }


@router.get("/risk/entities")
async def admin_risk_entities(current_user: dict = Depends(admin_guard),
                              db: AsyncSession = Depends(get_db)):
    """Risk entities across suppliers, users, and transactions."""
    entities = []
    vendors_all = (await db.execute(select(Vendor))).scalars().all()
    for v in vendors_all:
        score = risk_from_vendor(v)
        if score < 40:
            continue
        factors = []
        if v.verification_status != "verified":
            factors.append("Incomplete documentation")
        if v.total_reviews and v.total_reviews < 3:
            factors.append("Limited review history")
        if score > 60:
            factors.append("High risk score")
        entities.append({"id": str(v.id), "entity": v.business_name, "type": "Supplier",
                         "risk_score": score,
                         "category": "High Risk" if score > 60 else "Medium Risk",
                         "factors": factors[:3] or ["Reviewed by risk engine"],
                         "last_assessment": "Recently assessed"})
    suspended = (await db.execute(select(User).options(selectinload(User.profile)).where(User.status == "suspended"))).scalars().all()
    for u in suspended[:20]:
        name = f"{u.profile.first_name} {u.profile.last_name}".strip() if u.profile else u.email
        entities.append({"id": str(u.id), "entity": name, "type": "User", "risk_score": 65,
                         "category": "High Risk", "factors": ["Account suspended"],
                         "last_assessment": "Suspended"})
    refunded = (await db.execute(select(Order).where(Order.payment_status == "refunded").limit(20))).scalars().all()
    for o in refunded:
        entities.append({"id": str(o.id), "entity": f"Transaction #{o.order_number}",
                         "type": "Transaction", "risk_score": 60, "category": "Medium Risk",
                         "factors": ["Refunded transaction"], "last_assessment": "Refunded"})
    return {"entities": entities,
            "high_risk_count": sum(1 for e in entities if e["risk_score"] > 60),
            "medium_risk_count": sum(1 for e in entities if 40 <= e["risk_score"] <= 60),
            "low_risk_count": sum(1 for e in entities if e["risk_score"] < 40),
            "avg_risk_score": round(sum(e["risk_score"] for e in entities) / len(entities), 1) if entities else 0}


@router.get("/risk/trends")
async def admin_risk_trends(current_user: dict = Depends(admin_guard),
                            db: AsyncSession = Depends(get_db)):
    """Risk trend analysis (last 7 days) from audit logs referencing risk/vendor actions."""
    now = datetime.utcnow()
    trend = []
    for i in range(6, -1, -1):
        start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        events = (await db.execute(select(func.count(AuditLog.id)).where(
            AuditLog.created_at >= start, AuditLog.created_at < end,
            (AuditLog.action.ilike("%vendor%")) | (AuditLog.action.ilike("%verify%"))
            | (AuditLog.action.ilike("%risk%")) | (AuditLog.action.ilike("%approve%"))))).scalar() or 0
        trend.append({"date": start.strftime("%b %d"),
                      "highRisk": int(events * 0.4),
                      "mediumRisk": int(events * 0.35),
                      "lowRisk": max(0, events - int(events * 0.4) - int(events * 0.35))})
    return {"trend": trend}


@router.get("/analytics/overview")
async def admin_analytics_overview(current_user: dict = Depends(admin_guard),
                                   db: AsyncSession = Depends(get_db)):
    """Analytics overview: revenue, users, orders, category breakdown, growth."""
    now = datetime.utcnow()
    total_revenue = float((await db.execute(select(func.coalesce(func.sum(Order.total_amount), 0)))).scalar() or 0)
    active_users = (await db.execute(select(func.count(User.id)).where(User.status.in_(["active", "pending_verification"])))).scalar() or 0
    total_orders = (await db.execute(select(func.count(Order.id)))).scalar() or 0
    monthly_rev = []
    for i in range(5, -1, -1):
        start = (now - timedelta(days=i * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (now - timedelta(days=(i - 1) * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rev = float((await db.execute(select(func.coalesce(func.sum(Order.total_amount), 0)).where(Order.created_at >= start, Order.created_at < end))).scalar() or 0)
        monthly_rev.append({"month": start.strftime("%b"), "value": round(rev / 1_000_000, 1)})
    cat_rows = (await db.execute(select(Category.name, func.coalesce(func.sum(OrderItem.total_price), 0))
                .select_from(OrderItem).join(Product, OrderItem.product_id == Product.id)
                .join(Category, Product.category_id == Category.id).group_by(Category.name)
                .order_by(func.sum(OrderItem.total_price).desc()).limit(5))).all()
    total_cat_rev = sum(float(r[1] or 0) for r in cat_rows) or 1
    top_categories = [{"name": r[0], "revenue": f"₦{round(float(r[1] or 0) / 1_000_000, 1)}M",
                       "percentage": round((float(r[1] or 0) / total_cat_rev) * 100)} for r in cat_rows]
    role_counts = {}
    for role in UserRole:
        count = (await db.execute(select(func.count(User.id)).where(User.role == role))).scalar() or 0
        if count > 0:
            role_counts[role.value] = count
    total_roles = sum(role_counts.values()) or 1
    user_activity = [{"role": r.capitalize(), "count": c,
                      "percentage": round((c / total_roles) * 100)} for r, c in role_counts.items()]
    return {"total_revenue": total_revenue, "active_users": active_users, "total_orders": total_orders,
            "monthly_revenue": monthly_rev, "top_categories": top_categories, "user_activity": user_activity}
