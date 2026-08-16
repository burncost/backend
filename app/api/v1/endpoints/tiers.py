"""Vendor verification tier endpoints (Tier 1 CAC / Tier 2 Documented / Tier 3 Trusted)."""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, Dict

from app.core.database import get_db
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)
from app.api.deps import get_current_user, get_current_admin
from app.models.vendor import Vendor
from app.models.vendor_document import VendorDocument
from app.models.vendor_verification_tier import VendorVerificationTier
from app.models.user import User

router = APIRouter()


def _serialize(t: VendorVerificationTier) -> dict:
    return {
        "tier_code": t.tier_code,
        "display_name": t.display_name,
        "sort_order": t.sort_order,
        "transaction_cap": float(t.transaction_cap),
        "commission_rate": float(t.commission_rate),
        "required_document_types": t.required_document_types or [],
        "requires_manual_review": t.requires_manual_review,
        "perks": t.perks or [],
    }


@router.get("/tiers")
async def list_verification_tiers(db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(VendorVerificationTier)
        .where(VendorVerificationTier.is_active.is_(True))
        .order_by(VendorVerificationTier.sort_order)
    )
    return [_serialize(t) for t in res.scalars().all()]


@router.get("/me/eligibility")
async def upgrade_eligibility(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vend = (await db.execute(select(Vendor).where(Vendor.user_id == current_user.id))).scalar_one_or_none()
    if not vend:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Vendor profile not found")

    tiers = (await db.execute(
        select(VendorVerificationTier)
        .where(VendorVerificationTier.is_active.is_(True))
        .order_by(VendorVerificationTier.sort_order)
    )).scalars().all()

    current_rank = next((t.sort_order for t in tiers if t.tier_code == vend.verification_tier), 1)
    next_tier = next((t for t in tiers if t.sort_order > current_rank), None)
    if not next_tier:
        return {"tier": vend.verification_tier, "next_tier": None, "missing_documents": []}

    approved = {
        d.document_type for d in (await db.execute(
            select(VendorDocument).where(
                VendorDocument.vendor_id == vend.id,
                VendorDocument.tier == next_tier.tier_code,
                VendorDocument.review_status == "approved",
            )
        )).scalars().all()
    }
    required = set(next_tier.required_document_types or [])
    return {
        "tier": vend.verification_tier,
        "next_tier": _serialize(next_tier),
        "missing_documents": sorted(required - approved),
    }


@router.post("/upgrade")
async def upgrade_vendor_tier(
    payload: dict,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = (payload.get("tier_code") or "").strip()
    documents: Dict[str, str] = payload.get("documents") or {}
    tin = (payload.get("tin") or "").strip()
    if target not in ("documented", "trusted"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="target tier must be documented or trusted")

    vend = (await db.execute(select(Vendor).where(Vendor.user_id == current_user.id))).scalar_one_or_none()
    if not vend:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Vendor profile not found")

    tier = (await db.execute(
        select(VendorVerificationTier).where(VendorVerificationTier.tier_code == target)
    )).scalar_one_or_none()
    if not tier:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tier not found")

    required = set(tier.required_document_types or [])
    missing = [dt for dt in sorted(required) if not documents.get(dt)]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required documents for {target}: {', '.join(missing)}",
        )

    for dt, url in documents.items():
        if dt not in required:
            continue
        db.add(VendorDocument(
            vendor_id=vend.id,
            document_type=dt,
            document_url=url,
            tier=target,
            verified=False,
            review_status="pending",
        ))

    # Save TIN if provided (user can submit TIN later during tier upgrade)
    if tin:
        vend.tax_identification_number = tin

    # Tier 2 auto-ish: effective immediately. Tier 3: manual admin review.
    if target == "documented" and not tier.requires_manual_review:
        vend.verification_tier = "documented"
        vend.verification_status = "verified"
        manual = False
    else:
        vend.verification_tier = "trusted"
        vend.verification_status = "pending"
        manual = True

    await db.commit()

    # Send upgrade/review-submitted email to the vendor
    try:
        user = (await db.execute(select(User).where(User.id == vend.user_id))).scalar_one_or_none()
        if user and user.email:
            if not manual:
                await NotificationService().send_tier_upgrade_email(
                    email=user.email,
                    business_name=vend.business_name,
                    new_tier_name=tier.display_name,
                )
            else:
                await NotificationService().send_tier_review_submitted_email(
                    email=user.email,
                    business_name=vend.business_name,
                    tier_name=tier.display_name,
                )
    except Exception as e:
        logger.error(f"Failed to send tier upgrade email for vendor {vend.id}: {e}")

    return {
        "tier": vend.verification_tier,
        "verification_status": vend.verification_status,
        "requires_manual_review": manual,
    }


@router.patch("/admin/review/{vendor_id}")
async def review_vendor_documents(
    vendor_id: str,
    current_admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approve/reject pending upgrade documents (Tier 3 manual review)."""
    payload: Optional[dict] = None  # placeholder; use body via Body in practice
    return {"message": "use /admin/vendors/{id}/review-docs for full review"}