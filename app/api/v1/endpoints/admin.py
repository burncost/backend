"""Admin-facing endpoints — RBAC-protected reads of core platform data.

These power the Burncost Admin dashboard pages with real DB data
(Phase 2). All endpoints require an admin-tier role via `require_roles`.
"""
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload
from typing import Optional
from uuid import UUID
from datetime import datetime, timedelta

from app.core.database import get_db, get_mongodb
from app.api.deps import require_roles
from app.core.security import get_password_hash
from app.models.user import User, UserProfile, UserRole
from app.models.vendor import Vendor
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.audit_log import AuditLog
from app.repositories.boq_repository import BOQRepository
from app.services.risk_service import risk_from_vendor

import logging
import uuid

logger = logging.getLogger(__name__)

router = APIRouter()

# Every admin endpoint requires an admin-tier role (admin/super_admin always
# pass; manager/support/marketing are granted per-endpoint).
admin_guard = require_roles("manager", "support", "marketing")

# Only super_admin can create/manage other admin-tier users.
super_admin_guard = require_roles("super_admin")


class AdminCreateUser(BaseModel):
    """Payload for creating an admin-tier user."""
    email: EmailStr
    password: str
    role: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None


class AdminUpdateUser(BaseModel):
    """Payload for suspending/activating/role-changing an admin-tier user."""
    status: Optional[str] = None
    role: Optional[str] = None


def _admin_user_dict(u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "phone_number": u.phone_number,
        "role": u.role.value if hasattr(u.role, "value") else str(u.role),
        "status": u.status.value if hasattr(u.status, "value") else str(u.status),
        "email_verified": u.email_verified,
        "first_name": u.profile.first_name if u.profile else None,
        "last_name": u.profile.last_name if u.profile else None,
        "business_name": u.profile.business_name if u.profile else None,
        "location": u.profile.location if u.profile else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login": u.last_login.isoformat() if u.last_login else None,
    }


_ROLE_ENUM = {m.value: m for m in UserRole}


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def admin_create_user(
    payload: AdminCreateUser,
    current_user: dict = Depends(super_admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Create an admin-tier (or any role) user. super_admin only."""
    role = payload.role.strip().lower()
    if role not in _ROLE_ENUM:
        raise HTTPException(400, detail=f"role must be one of: {', '.join(_ROLE_ENUM)}")

    existing = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(400, detail="Email already registered")

    now = datetime.utcnow()
    user = User(
        email=str(payload.email).lower(),
        phone_number=payload.phone_number or f"999{uuid.uuid4().hex[:7]}",
        password_hash=get_password_hash(payload.password),
        role=_ROLE_ENUM[role],
        status="active",
        email_verified=True,
        phone_verified=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        await db.flush()
        profile = UserProfile(
            user_id=user.id,
            first_name=payload.first_name or "Admin",
            last_name=payload.last_name or "User",
        )
        db.add(profile)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "User with this information already exists")

    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user.id)
    )
    return _admin_user_dict(result.scalar_one())


@router.patch("/users/{user_id}")
async def admin_update_user(
    user_id: UUID,
    payload: AdminUpdateUser,
    current_user: dict = Depends(super_admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Suspend/activate/change role of a user. super_admin only."""
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    if payload.status is not None:
        if payload.status not in ("active", "suspended", "deactivated", "pending_verification"):
            raise HTTPException(400, "invalid status")
        u.status = payload.status
    if payload.role is not None:
        role = payload.role.strip().lower()
        if role not in _ROLE_ENUM:
            raise HTTPException(400, f"role must be one of: {', '.join(_ROLE_ENUM)}")
        u.role = _ROLE_ENUM[role]

    await db.commit()
    await db.refresh(u)
    return _admin_user_dict(u)


@router.get("/stats")
async def admin_overview_stats(
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Dashboard overview: platform-wide counts."""
    users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    vendors = (await db.execute(select(func.count(Vendor.id)))).scalar() or 0
    products = (await db.execute(select(func.count(Product.id)))).scalar() or 0
    orders = (await db.execute(select(func.count(Order.id)))).scalar() or 0

    # Escrow-ish: orders with completed payment that are held/processing
    held_orders = (
        await db.execute(
            select(func.count(Order.id)).where(
                Order.status.in_(["confirmed", "processing", "ready_for_pickup", "shipped", "in_transit"])
            )
        )
    ).scalar() or 0

    # Refund requests: orders with refunded payment status or cancelled-with-refund
    refund_requests = (
        await db.execute(
            select(func.count(Order.id)).where(Order.payment_status == "refunded")
        )
    ).scalar() or 0

    # Verification pipeline
    pending_vendors = (
        await db.execute(
            select(func.count(Vendor.id)).where(Vendor.verification_status == "pending")
        )
    ).scalar() or 0
    verified_vendors = (
        await db.execute(
            select(func.count(Vendor.id)).where(Vendor.verification_status == "verified")
        )
    ).scalar() or 0
    # High-risk count uses the shared risk score (High bucket = score > 60).
    _all_vendors = (await db.execute(select(Vendor))).scalars().all()
    high_risk_vendors = sum(1 for v in _all_vendors if risk_from_vendor(v) > 60)

    # Gross merchandise value (delivered orders)
    gmv = (
        await db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(Order.status == "delivered")
        )
    ).scalar() or 0

    # Recent audit activity (last 7 days)
    audit_recent = (
        await db.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.created_at >= datetime.utcnow() - timedelta(days=7)
            )
        )
    ).scalar() or 0

    return {
        "total_users": users,
        "total_vendors": vendors,
        "total_products": products,
        "total_orders": orders,
        "held_payments": held_orders,
        "refund_requests": refund_requests,
        "pending_vendors": pending_vendors,
        "verified_vendors": verified_vendors,
        "high_risk_vendors": high_risk_vendors,
        "gmv": float(gmv),
        "audit_events_7d": audit_recent,
    }


@router.get("/users")
async def admin_list_users(
    role: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """All platform users (Customer, Vendor, admin tiers)."""
    query = select(User).options(selectinload(User.profile))
    count_query = select(func.count(User.id))

    if role:
        query = query.where(User.role == role)
        count_query = count_query.where(User.role == role)
    if status_filter:
        query = query.where(User.status == status_filter)
        count_query = count_query.where(User.status == status_filter)
    if search:
        like = f"%{search}%"
        query = query.where(
            (User.email.ilike(like))
            | (UserProfile.first_name.ilike(like))
            | (UserProfile.last_name.ilike(like))
            | (UserProfile.business_name.ilike(like))
        )
        count_query = count_query.join(UserProfile).where(
            (User.email.ilike(like))
            | (UserProfile.first_name.ilike(like))
            | (UserProfile.last_name.ilike(like))
            | (UserProfile.business_name.ilike(like))
        )

    total = (await db.execute(count_query)).scalar() or 0

    query = (
        query.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    users = result.scalars().all()

    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "phone_number": u.phone_number,
                "role": u.role.value if hasattr(u.role, "value") else str(u.role),
                "status": u.status.value if hasattr(u.status, "value") else str(u.status),
                "email_verified": u.email_verified,
                "first_name": u.profile.first_name if u.profile else None,
                "last_name": u.profile.last_name if u.profile else None,
                "business_name": u.profile.business_name if u.profile else None,
                "location": u.profile.location if u.profile else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login": u.last_login.isoformat() if u.last_login else None,
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/vendors")
async def admin_list_vendors(
    verification: Optional[str] = Query(None, alias="verification_status"),
    search: Optional[str] = Query(None),
    risk_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """All supplier/vendor applications (verification pipeline)."""
    query = select(Vendor).options(selectinload(Vendor.products))
    count_query = select(func.count(Vendor.id))

    if verification:
        query = query.where(Vendor.verification_status == verification)
        count_query = count_query.where(Vendor.verification_status == verification)
    if search:
        like = f"%{search}%"
        query = query.where(
            (Vendor.business_name.ilike(like))
            | (Vendor.cac_business_registration_number.ilike(like))
        )
        count_query = count_query.where(
            (Vendor.business_name.ilike(like))
            | (Vendor.cac_business_registration_number.ilike(like))
        )

    total = (await db.execute(count_query)).scalar() or 0

    # risk_only filters on the shared risk score (High bucket = score > 60),
    # so we must score candidates in Python to keep the count consistent.
    if risk_only:
        all_candidates = (await db.execute(
            query.order_by(Vendor.created_at.desc())
        )).scalars().all()
        all_candidates = [v for v in all_candidates if risk_from_vendor(v) > 60]
        total = len(all_candidates)
        start = (page - 1) * page_size
        vendors = all_candidates[start:start + page_size]
    else:
        query = (
            query.order_by(Vendor.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(query)
        vendors = result.scalars().all()

    return {
        "vendors": [
            {
                "id": str(v.id),
                "user_id": str(v.user_id),
                "business_name": v.business_name,
                "business_type": v.business_type,
                "city": v.city,
                "state": v.state,
                "business_address": v.business_address,
                "cac_number": v.cac_business_registration_number,
                "verification_status": v.verification_status.value if hasattr(v.verification_status, "value") else str(v.verification_status),
                "verification_date": v.verification_date.isoformat() if v.verification_date else None,
                "rating": float(v.rating) if v.rating else 0.0,
                "total_reviews": v.total_reviews or 0,
                "total_sales": float(v.total_sales) if v.total_sales else 0.0,
                "is_featured": v.is_featured or False,
                "delivery_time": v.delivery_time,
                "response_time": v.response_time,
                "specializations": v.specializations or [],
                "risk_score": risk_from_vendor(v),
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in vendors
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/orders")
async def admin_list_orders(
    order_status: Optional[str] = Query(None, alias="order_status"),
    payment_status: Optional[str] = Query(None, alias="payment_status"),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """All orders — powers Escrow Payments + Disputes views."""
    query = select(Order).options(selectinload(Order.user).selectinload(User.profile))
    count_query = select(func.count(Order.id))

    if order_status:
        query = query.where(Order.status == order_status)
        count_query = count_query.where(Order.status == order_status)
    if payment_status:
        query = query.where(Order.payment_status == payment_status)
        count_query = count_query.where(Order.payment_status == payment_status)
    if search:
        like = f"%{search}%"
        query = query.where(Order.order_number.ilike(like))
        count_query = count_query.where(Order.order_number.ilike(like))

    total = (await db.execute(count_query)).scalar() or 0

    query = (
        query.order_by(Order.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    orders = result.scalars().all()

    # Escrow status mapping based on payment + delivery lifecycle
    def _escrow_status(o: Order) -> str:
        if o.payment_status == "refunded":
            return "Refund Requested"
        if o.payment_status == "completed":
            if o.status in ("delivered", "refunded"):
                return "Funds Released"
            return "Payment Held"
        if o.status == "cancelled" and o.payment_status == "completed":
            return "Refund Requested"
        return "Payment Held"

    return {
        "orders": [
            {
                "id": str(o.id),
                "order_number": o.order_number,
                "buyer": f"{o.user.profile.first_name} {o.user.profile.last_name}".strip() if o.user and o.user.profile else (o.user.email if o.user else "—"),
                "buyer_email": o.user.email if o.user else None,
                "amount": float(o.total_amount),
                "subtotal": float(o.subtotal),
                "shipping_fee": float(o.shipping_fee or 0),
                "tax_amount": float(o.tax_amount or 0),
                "discount_amount": float(o.discount_amount or 0),
                "order_status": o.status.value if hasattr(o.status, "value") else str(o.status),
                "payment_status": o.payment_status.value if hasattr(o.payment_status, "value") else str(o.payment_status),
                "payment_method": o.payment_method.value if hasattr(o.payment_method, "value") else None,
                "escrow_status": _escrow_status(o),
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "estimated_delivery_date": o.estimated_delivery_date.isoformat() if o.estimated_delivery_date else None,
                "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
            }
            for o in orders
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/products")
async def admin_list_products(
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """All products across the marketplace."""
    query = select(Product).options(selectinload(Product.vendor))
    count_query = select(func.count(Product.id))

    if search:
        like = f"%{search}%"
        query = query.where(
            (Product.name.ilike(like)) | (Product.sku.ilike(like))
        )
        count_query = count_query.where(
            (Product.name.ilike(like)) | (Product.sku.ilike(like))
        )

    total = (await db.execute(count_query)).scalar() or 0

    query = (
        query.order_by(Product.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    products = result.scalars().all()

    return {
        "products": [
            {
                "id": str(p.id),
                "name": p.name,
                "slug": p.slug,
                "sku": p.sku,
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                "base_price": float(p.base_price),
                "discount_price": float(p.discount_price) if p.discount_price else None,
                "quantity": p.quantity,
                "sales_count": p.sales_count,
                "rating": float(p.rating) if p.rating else 0.0,
                "review_count": p.review_count,
                "vendor": p.vendor.business_name if p.vendor else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in products
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/audit-logs")
async def admin_list_audit_logs(
    user_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    """Audit trail viewer (from the AuditLog table)."""
    query = select(AuditLog)
    count_query = select(func.count(AuditLog.id))

    if user_id:
        query = query.where(AuditLog.user_id == UUID(user_id))
        count_query = count_query.where(AuditLog.user_id == UUID(user_id))
    if action:
        like = f"%{action}%"
        query = query.where(AuditLog.action.ilike(like))
        count_query = count_query.where(AuditLog.action.ilike(like))

    total = (await db.execute(count_query)).scalar() or 0

    query = (
        query.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    entries = result.scalars().all()

    return {
        "audit_logs": [
            {
                "id": str(e.id),
                "user_id": str(e.user_id) if e.user_id else None,
                "action": e.action,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "method": e.method,
                "path": e.path,
                "status_code": e.status_code,
                "ip_address": e.ip_address,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/boqs")
async def admin_list_boqs(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(admin_guard),
    mongodb=Depends(get_mongodb),
):
    """BOQ AI Analysis overview (MongoDB)."""
    if mongodb is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BOQ store is unavailable",
        )

    repo = BOQRepository(mongodb)
    query = {}
    if status_filter:
        query["status"] = status_filter

    total = await repo.count(query)
    skip = (page - 1) * page_size
    docs = await repo.find(query, skip=skip, limit=page_size, sort=[("createdAt", -1)])

    return {
        "boqs": [
            {
                "id": str(doc.get("_id")),
                "title": doc.get("title") or doc.get("projectName"),
                "status": doc.get("status"),
                "createdAt": doc.get("createdAt"),
                "createdBy": doc.get("createdBy"),
                "version": doc.get("version"),
                "confidence": doc.get("confidence"),
                "totalItems": doc.get("totalItems"),
                "flaggedItems": doc.get("flaggedItems"),
                "totalValue": doc.get("totalValue"),
            }
            for doc in docs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }