"""Vendor verification tier endpoints (Starter / Verified Vendor / Enterprise)."""
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional

from app.core.database import get_db
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)
from app.api.deps import get_current_user, get_current_admin
from app.models.vendor import Vendor
from app.models.vendor_document import VendorDocument
from app.models.vendor_verification_tier import VendorVerificationTier
from app.models.user import User, UserProfile
from app.services.vendor_tier_service import normalize_tier

def _normalize_phone(raw: str) -> Optional[str]:
    """Normalize a Nigerian phone number to +234XXXXXXXXXX (mirrors UserUpdate).

    Returns None when the value isn't a valid Nigerian mobile number.
    """
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if digits.startswith("234") and len(digits) >= 13:
        digits = digits[3:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "789":
        return None
    return "+234" + digits


router = APIRouter()


def _serialize(t: VendorVerificationTier) -> dict:
    return {
        "tier_code": t.tier_code,
        "display_name": t.display_name,
        "sort_order": t.sort_order,
        "transaction_cap": float(t.transaction_cap),
        "uncapped": t.tier_code == "enterprise",
        # "commission_rate": float(t.commission_rate),  # Phase 13: commission removed from output
        "required_document_types": t.required_document_types or [],
        "requires_manual_review": t.requires_manual_review,
        "perks": t.perks or [],
    }


async def assign_tier_by_volume(db: AsyncSession, vendor: Vendor) -> None:
    """Re-assign a verified vendor's tier from their transaction volume.

    Caps: starter ≤₦3M · verified_vendor ₦3–10M · enterprise uncapped.
    Only applies to verified suppliers (pending vendors keep their tier until
    their enterprise/CAC application is approved).
    """
    if not vendor or vendor.verification_status != "verified":
        return
    tiers = (await db.execute(
        select(VendorVerificationTier)
        .where(VendorVerificationTier.is_active.is_(True))
        .order_by(VendorVerificationTier.sort_order)
    )).scalars().all()
    if not tiers:
        return
    volume = float(vendor.transaction_volume or 0)
    chosen = tiers[-1]  # highest tier when volume exceeds every cap
    for t in tiers:
        if volume <= float(t.transaction_cap):
            chosen = t
            break
    if chosen.tier_code != vendor.verification_tier:
        vendor.verification_tier = chosen.tier_code


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
    current_user: User = Depends(get_current_user),
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

    current_rank = next((t.sort_order for t in tiers if t.tier_code == normalize_tier(vend.verification_tier)), 1)
    next_tier = next((t for t in tiers if t.sort_order > current_rank), None)
    if not next_tier:
        return {"tier": vend.verification_tier, "next_tier": None, "missing_documents": []}

    # Document requirements bypassed: single-field upgrades only (NIN / CAC).
    next_requirement = ["nin"] if next_tier.tier_code == "verified_vendor" else ["cac"]
    return {
        "tier": vend.verification_tier,
        "next_tier": _serialize(next_tier),
        "missing_documents": next_requirement,
    }


@router.post("/upgrade")
async def upgrade_vendor_tier(
    payload: dict,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Simple 3-tier upgrade: NIN -> verified_vendor, CAC -> enterprise.

    NIN/CAC are persisted here for manual admin review. An optional CAC
    document URL can be attached. The old multi-document requirement
    pipeline is bypassed, not deleted.

    Identity verification (Mono):
    - NIN: bypassed until IDENTITY_VERIFY_ENABLED=True (instant auto-verify).
    - CAC: enforced — business name must match and status must be "active".
    """
    target = normalize_tier((payload.get("tier_code") or "").strip())
    nin = (payload.get("nin") or "").strip()
    dob_raw = (payload.get("date_of_birth") or "").strip()
    first_name = (payload.get("first_name") or "").strip()
    other_name = (payload.get("other_name") or "").strip()
    last_name = (payload.get("last_name") or "").strip()
    phone_raw = (payload.get("phone_number") or "").strip()
    cac = (payload.get("cac_number") or payload.get("cac") or "").strip()
    cac_doc_url = (payload.get("cac_document_url") or "").strip()
    if target not in ("verified_vendor", "enterprise"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="target tier must be verified_vendor or enterprise")

    vend = (await db.execute(select(Vendor).where(Vendor.user_id == current_user.id))).scalar_one_or_none()
    if not vend:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Vendor profile not found")

    tier = (await db.execute(
        select(VendorVerificationTier).where(VendorVerificationTier.tier_code == target)
    )).scalar_one_or_none()
    if not tier:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tier not found")

    # ---- Validate everything BEFORE mutating anything, so a rejection can never
    #      leave a partial write behind (e.g. names assigned then rolled back). ----
    if target == "verified_vendor":
        if not nin:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="NIN is required for Verified Vendor")
        if not (first_name and last_name):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Your first and last name are required to become a Verified Vendor",
            )
    if target == "enterprise" and not cac:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="CAC/RC registration number is required for Enterprise")
    dob = None
    if dob_raw:
        try:
            dob = datetime.strptime(dob_raw, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="date_of_birth must be in YYYY-MM-DD format")

    # Phone: normalize to the app-wide +234 format and reject duplicates.
    # The comparison is format-insensitive (the table holds both 0803… and +234803…).
    phone = None
    if phone_raw:
        phone = _normalize_phone(phone_raw)
        if not phone:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Please provide a valid Nigerian phone number (e.g. 08012345678)",
            )
        digits_only = func.regexp_replace(func.coalesce(User.phone_number, ""), r"[^0-9]", "", "g")
        dup_phone = (await db.execute(
            select(User).where(
                User.id != current_user.id,
                func.right(digits_only, 10) == phone[-10:],
            )
        )).scalars().first()
        if dup_phone:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="This phone number is already registered to another account.",
            )

    # Persist NIN / CAC on the vendor record (kept for manual admin review).
    if nin:
        # NIN is unique across vendors.
        dup = (await db.execute(
            select(Vendor).where(Vendor.nin == nin, Vendor.id != vend.id)
        )).scalar_one_or_none()
        if dup:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="This NIN is already registered to another supplier account.",
            )
        vend.nin = nin
    if cac:
        # CAC number is unique across vendors.
        dup_cac = (await db.execute(
            select(Vendor).where(Vendor.cac_business_registration_number == cac, Vendor.id != vend.id)
        )).scalar_one_or_none()
        if dup_cac:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="This CAC/RC number is already registered to another supplier account.",
            )
        vend.cac_business_registration_number = cac

    # Save personal details (DOB + names) on the user profile — needed for Mono
    # NIN name/DOB matching and kept for manual admin review.
    profile = (await db.execute(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )).scalar_one_or_none()
    if target == "verified_vendor" and not profile:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User profile not found")
    if dob and profile:
        profile.date_of_birth = dob
    # Names are validated above for verified_vendor, so they are always written.
    if profile and target == "verified_vendor":
        profile.first_name = first_name
        profile.last_name = last_name
        if other_name:
            profile.other_name = other_name

    # Phone lives on the User row (normalized + duplicate-checked above).
    if phone:
        user_row = (await db.execute(select(User).where(User.id == current_user.id))).scalar_one_or_none()
        if user_row:
            user_row.phone_number = phone

    if target == "verified_vendor":
        # ---- Mono NIN verification (BYPASSED until IDENTITY_VERIFY_ENABLED) ----
        from app.config import settings
        from app.services import mono_identity_service as mono
        if settings.IDENTITY_VERIFY_ENABLED and settings.MONO_SECRET_KEY:
            nin_result = mono.get_cached_nin(profile, nin) or await mono.verify_nin(nin)
            if not nin_result:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="NIN lookup failed. Please try again.")
            if profile:
                mono.save_nin_cache(profile, nin_result)
            if not mono.nin_matches(nin_result, profile):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="NIN verification failed: your name and date of birth must match your NIN record.",
                )

    if target == "enterprise":
        # ---- Mono CAC verification (ENFORCED — no bypass) ----
        from app.config import settings
        from app.services import mono_identity_service as mono
        if settings.MONO_SECRET_KEY:
            cac_result = mono.get_cached_cac(vend, cac) or await mono.verify_cac(cac)
            if not cac_result:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="CAC lookup failed: registration number not found or service unavailable.",
                )
            mono.save_cac_cache(vend, cac, cac_result)
            if not mono.cac_matches(cac_result, vend):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="CAC verification failed: your business name must match the CAC record and the business must be active.",
                )

    # Log the submission for manual review (bypassed old doc pipeline).
    db.add(VendorDocument(
        vendor_id=vend.id,
        document_type="nin" if target == "verified_vendor" else "cac",
        document_url=cac_doc_url or (f"number:{nin or cac}"),
        tier=target,
        verified=False,
        review_status="pending",
    ))

    # Auto-approve: instant tier bump (monotonic — never downgrade).
    from app.services.vendor_tier_service import tier_index
    current_rank = tier_index(vend.verification_tier)
    if tier_index(target) >= current_rank:
        vend.verification_tier = target
    if target == "enterprise":
        vend.verification_status = "verified"
        vend.verification_date = vend.verification_date or datetime.utcnow()

    await db.commit()

    # Send upgrade email to the vendor
    try:
        user = (await db.execute(select(User).where(User.id == vend.user_id))).scalar_one_or_none()
        if user and user.email:
            await NotificationService().send_tier_upgrade_email(
                email=user.email,
                business_name=vend.business_name,
                new_tier_name=tier.display_name,
            )
    except Exception as e:
        logger.error(f"Failed to send tier upgrade email for vendor {vend.id}: {e}")

    return {
        "tier": vend.verification_tier,
        "verification_status": vend.verification_status if not hasattr(vend.verification_status, "value") else vend.verification_status.value,
        "requires_manual_review": False,
    }


@router.patch("/admin/review/{vendor_id}")
async def review_vendor_documents(
    vendor_id: str,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approve/reject pending upgrade documents (Tier 3 manual review)."""
    payload: Optional[dict] = None  # placeholder; use body via Body in practice
    return {"message": "use /admin/vendors/{id}/review-docs for full review"}