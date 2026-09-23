"""Admin action queue + review for vendor verification tiers."""
import logging
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Optional

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.vendor import Vendor, VendorVerificationStatus
from app.models.vendor_document import VendorDocument
from app.models.vendor_bank_account import VendorBankAccount
from app.models.order import Order, OrderItem
from app.models.notification import Notification
from app.models.user import User, UserProfile
from app.services.risk_service import risk_from_vendor

logger = logging.getLogger(__name__)
router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")

_TIER_RANK = {"starter": 0, "verified_vendor": 1, "enterprise": 2}


def _status(v):
    return v.verification_status.value if hasattr(v.verification_status, "value") else str(v.verification_status)


def _kind(v, doc_map, held_count=0):
    has = bool(doc_map)
    status = _status(v)
    tier = v.verification_tier if v.verification_tier in _TIER_RANK else (
        "starter" if v.verification_tier in ("cac_only", "tier_0", "tier_1") else
        "verified_vendor" if v.verification_tier in ("documented", "tier_2") else "enterprise"
    )
    if held_count > 0:
        return "cap_hold"
    # NIN/CAC submissions are auto-approved; they surface here for manual audit only.
    if tier == "starter" and "nin" in doc_map:
        return "tier2_upgrade"
    if tier in ("verified_vendor", "starter") and "cac" in doc_map:
        return "tier3_upgrade"
    if status == "pending":
        return "vendor_basic"
    return None


@router.get("/vendors/actions")
async def admin_action_queue(
    kind: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(admin_guard),
    db: AsyncSession = Depends(get_db),
):
    _KIND_ORDER = {"cap_hold": 0, "tier2_upgrade": 1, "tier3_upgrade": 2, "vendor_basic": 3}

    vendors = (await db.execute(select(Vendor))).scalars().all()
    actions = []
    for v in vendors:
        docs = (await db.execute(select(VendorDocument).where(VendorDocument.vendor_id == v.id))).scalars().all()
        doc_map_pending = {d.document_type: d.document_url for d in docs if d.review_status == "pending"}
        held_count = (await db.execute(select(func.count(Order.id)).join(OrderItem, OrderItem.order_id == Order.id).where(OrderItem.vendor_id == v.id, Order.status == "on_hold"))).scalar() or 0
        k = _kind(v, doc_map_pending, held_count=held_count)
        if not k or (kind and k != kind):
            continue
        actions.append({
            "vendor_id": str(v.id),
            "business_name": v.business_name,
            "city": v.city, "state": v.state,
            "cac_number": v.cac_business_registration_number,
            "nin": v.nin,
            "verification_status": _status(v),
            "verification_tier": v.verification_tier,
            "transaction_volume": float(v.transaction_volume or 0),
            "kind": k,
            "pending_documents": list(doc_map_pending.keys()),
            "held_orders": int(held_count),
            "_created_at": v.created_at,
        })

    # Priority order: cap_hold → tier1_manual → tier2_upgrade → tier3_upgrade → vendor_basic;
    # then newest first within the same kind.
    actions.sort(key=lambda a: (_KIND_ORDER.get(a["kind"], 99), -(a["_created_at"].timestamp() if a["_created_at"] else 0)))
    for a in actions:
        a.pop("_created_at", None)

    start = (page - 1) * page_size
    return {
        "actions": actions[start:start + page_size],
        "total": len(actions), "page": page, "page_size": page_size,
        "total_pages": max(1, (len(actions) + page_size - 1) // page_size),
    }


@router.get("/vendors/{vendor_id}/detail")
async def admin_vendor_detail(
    vendor_id: UUID, current_user: dict = Depends(admin_guard), db: AsyncSession = Depends(get_db)
):
    """Full vendor details for the admin review screen."""
    vendor = (await db.execute(
        select(Vendor)
        .options(
            selectinload(Vendor.user).selectinload(User.profile),
            selectinload(Vendor.bank_accounts),
            selectinload(Vendor.documents),
        )
        .where(Vendor.id == vendor_id)
    )).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Vendor not found")

    user = vendor.user
    profile = user.profile if user else None

    vstatus = _status(vendor)
    risk_score = risk_from_vendor(vendor)

    return {
        "id": str(vendor.id),
        "user_id": str(vendor.user_id),
        "business_name": vendor.business_name,
        "business_type": vendor.business_type,
        "city": vendor.city,
        "state": vendor.state,
        "business_address": vendor.business_address,
        "cac_number": vendor.cac_business_registration_number,
        "nin": vendor.nin,
        "verification_status": vstatus,
        "verification_tier": vendor.verification_tier,
        "verification_date": vendor.verification_date.isoformat() if vendor.verification_date else None,
        "rating": float(vendor.rating) if vendor.rating else 0.0,
        "total_reviews": vendor.total_reviews or 0,
        "total_sales": float(vendor.total_sales) if vendor.total_sales else 0.0,
        "business_image": vendor.business_image,
        "transaction_volume": float(vendor.transaction_volume or 0),
        "risk_score": risk_score,
        "created_at": vendor.created_at.isoformat() if vendor.created_at else None,
        "contact": {
            "first_name": profile.first_name if profile else None,
            "last_name": profile.last_name if profile else None,
            "email": user.email if user else None,
            "phone_number": user.phone_number if user else None,
        },
        "bank_accounts": [
            {
                "bank_name": b.bank_name,
                "account_number": b.account_number,
                "account_name": b.account_name,
                "is_primary": b.is_primary,
                "verified": b.verified,
            }
            for b in vendor.bank_accounts
        ],
        "documents": [
            {
                "id": str(d.id),
                "document_type": d.document_type,
                "document_url": d.document_url,
                "tier": d.tier,
                "review_status": d.review_status,
                "verified": d.verified,
                "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
            }
            for d in vendor.documents
        ],
    }


@router.get("/vendors/actions/{vendor_id}/documents")
async def admin_vendor_documents(vendor_id: UUID, current_user: dict = Depends(admin_guard), db: AsyncSession = Depends(get_db)):
    docs = (await db.execute(select(VendorDocument).where(VendorDocument.vendor_id == vendor_id))).scalars().all()
    return [{
        "id": str(d.id), "document_type": d.document_type, "document_url": d.document_url,
        "tier": d.tier, "review_status": d.review_status,
        "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
    } for d in docs]


@router.post("/vendors/actions/{vendor_id}/review")
async def admin_review_vendor_action(
    vendor_id: UUID, payload: dict,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(admin_guard), db: AsyncSession = Depends(get_db),
):
    action = (payload.get("action") or "").strip().lower()
    target = (payload.get("tier") or "").strip()
    if action not in ("approve", "reject", "resolve_cap"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="action must be approve, reject, resolve_cap")

    v = (await db.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one_or_none()
    if not v:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Vendor not found")

    now = datetime.utcnow()
    if action == "approve":
        if target not in _TIER_RANK:
            docs = (await db.execute(select(VendorDocument).where(
                VendorDocument.vendor_id == v.id, VendorDocument.review_status == "pending"))).scalars().all()
            target = max([d.tier for d in docs], key=lambda t: _TIER_RANK.get(t, 0), default="starter")
        v.verification_tier = target
        v.verification_status = VendorVerificationStatus.VERIFIED
        v.verification_date = now
        # Phase 13: on admin approval, assign the tier that matches volume.
        from app.api.v1.endpoints.tiers import assign_tier_by_volume
        await assign_tier_by_volume(db, v)
        pending = (await db.execute(select(VendorDocument).where(
            VendorDocument.vendor_id == v.id, VendorDocument.tier == target,
            VendorDocument.review_status == "pending"))).scalars().all()
        for d in pending:
            d.review_status = "approved"; d.verified = True; d.reviewed_at = now
    elif action == "reject":
        v.verification_status = VendorVerificationStatus.REJECTED
        pending = (await db.execute(select(VendorDocument).where(
            VendorDocument.vendor_id == v.id, VendorDocument.review_status == "pending"))).scalars().all()
        for d in pending:
            d.review_status = "rejected"; d.reviewed_at = now
    else:  # resolve_cap
        held = (await db.execute(select(Order).join(OrderItem, OrderItem.order_id == Order.id).where(OrderItem.vendor_id == v.id, Order.status == "on_hold").distinct())).scalars().all()
        for o in held:
            o.status = "confirmed"
        return {"message": "Held orders released", "released": len(held)}

    db.add(Notification(user_id=v.user_id, title="Verification update",
                        message=f"Your {target} verification was {action}d.", type="verification", read=False))
    await db.commit()

    # Send email to the vendor when approved/rejected
    try:
        vendor_user = (await db.execute(select(User).where(User.id == v.user_id))).scalar_one_or_none()
        if vendor_user and vendor_user.email:
            from app.services.notification_service import NotificationService
            if action == "approve":
                if _TIER_RANK.get(target, 0) > 0:
                    # Tier upgrade approval
                    from app.models.vendor_verification_tier import VendorVerificationTier
                    tier_row = (await db.execute(
                        select(VendorVerificationTier).where(VendorVerificationTier.tier_code == target)
                    )).scalar_one_or_none()
                    tier_name = tier_row.display_name if tier_row else target
                    background_tasks.add_task(
                        NotificationService().send_tier_upgrade_email,
                        email=vendor_user.email,
                        business_name=v.business_name,
                        new_tier_name=tier_name,
                    )
                else:
                    # New vendor verification approval
                    background_tasks.add_task(
                        NotificationService().send_vendor_verified_email,
                        email=vendor_user.email,
                        business_name=v.business_name,
                    )
            elif action == "reject":
                background_tasks.add_task(
                    NotificationService().send_vendor_rejection_email,
                    email=vendor_user.email,
                    business_name=v.business_name,
                )
    except Exception as e:
        logger.error(f"Failed to send verification email for vendor {vendor_id}: {e}")

    return {"vendor_id": str(v.id), "verification_tier": v.verification_tier,
            "verification_status": v.verification_status.value if hasattr(v.verification_status, "value") else str(v.verification_status)}
