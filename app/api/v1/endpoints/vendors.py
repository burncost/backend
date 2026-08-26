from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from uuid import UUID
from typing import Optional
from datetime import datetime, timezone
import asyncio

from app.core.database import get_db
from app.models.vendor import Vendor
from app.models.user import User, UserProfile
from app.models.notification import Notification
from app.models.vendor_draft import VendorDraft
from app.schemas.vendor import VendorCreate, VendorUpdate, VendorResponse
from app.services.cloudinary_upload_service import upload_image_to_cloudinary
from app.schemas.vendor_draft import VendorDraftSave, VendorDraftResponse
from app.api.deps import get_current_user
from app.services.auth_service import AuthService
import logging
import json

router = APIRouter()
logger = logging.getLogger(__name__)
auth_service = AuthService()

# In-memory cache for CAC verification results (keyed by user_id string)
_cac_cache: dict[str, dict] = {}

def _is_cac_result_complete(result: dict) -> bool:
    return bool(
        result.get("business_name")
        and result.get("tax_id")
        and result.get("status")
    )


async def _auto_verify_vendor_background(
    vendor_id: str,
    rc_number: str,
    submitted_tin: str,
) -> None:
    """Run CAC auto-verification after onboarding has committed.

    Flips the same vendor row from `pending` → `verified` when the CAC
    lookup succeeds and the submitted TIN matches the CAC record with an
    ACTIVE status. If anything fails, the vendor stays `pending` for admin
    review (handled by the admin Approve/Reject UI).
    """
    from app.core.database import AsyncSessionLocal
    from app.tasks.cac_tasks import get_cac_business_info

    try:
        async with AsyncSessionLocal() as db:
            for attempt in range(2):
                cac_data = await asyncio.to_thread(get_cac_business_info, rc_number)
                logger.info(
                    f"[auto-verify] attempt {attempt + 1} RC={rc_number}: "
                    f"complete={_is_cac_result_complete(cac_data)}"
                )
                if _is_cac_result_complete(cac_data):
                    break
                if attempt == 0:
                    await asyncio.sleep(1.5)

            if not _is_cac_result_complete(cac_data):
                logger.warning(f"[auto-verify] incomplete CAC data for RC={rc_number}; staying pending")
                return

            submitted_tin_norm = submitted_tin.strip().replace("-", "").replace(" ", "")
            cac_tin = (cac_data.get("tax_id") or "").strip()
            tax_id_matches = bool(submitted_tin_norm and cac_tin and submitted_tin_norm == cac_tin)
            status_active = (cac_data.get("status") or "").upper() == "ACTIVE"

            if not (tax_id_matches and status_active):
                logger.info(
                    f"[auto-verify] vendor {vendor_id} did not pass auto-verification "
                    f"(tax_match={tax_id_matches}, active={status_active}); staying pending"
                )
                return

            # Reload the vendor (it may have been admin-modified meanwhile)
            result = await db.execute(select(Vendor).where(Vendor.id == UUID(vendor_id)))
            vendor = result.scalar_one_or_none()
            if not vendor:
                logger.warning(f"[auto-verify] vendor {vendor_id} not found; skipping")
                return
            if vendor.verification_status != "pending":
                return  # Already approved/rejected/deactivated by an admin

            vendor.verification_status = "verified"
            vendor.verification_date = datetime.now(timezone.utc).replace(tzinfo=None)
            # Phase 13: assign the tier matching current transaction volume.
            from app.api.v1.endpoints.tiers import assign_tier_by_volume
            await assign_tier_by_volume(db, vendor)
            await db.commit()

            # Notify the vendor that they're verified
            vendor_user_id = vendor.user_id
            try:
                db.add(Notification(
                    user_id=vendor_user_id,
                    type="verification",
                    title="You're verified!",
                    message="Congratulations! Your supplier account has been verified and is now live on the marketplace.",
                    read=False,
                ))
                await db.commit()
            except Exception as e:
                logger.error(f"[auto-verify] failed to notify vendor {vendor_id}: {e}")

            # Send "account verified" email to the vendor
            try:
                vendor_user = (await db.execute(select(User).where(User.id == vendor_user_id))).scalar_one_or_none()
                if vendor_user and vendor_user.email:
                    from app.services.notification_service import NotificationService
                    await NotificationService().send_vendor_verified_email(
                        email=vendor_user.email,
                        business_name=vendor.business_name,
                    )
            except Exception as e:
                logger.error(f"[auto-verify] failed to email vendor {vendor_id}: {e}")

            admin_result = await db.execute(select(User).where(User.role.in_(["admin", "super_admin"])))
            for admin in admin_result.scalars().all():
                db.add(Notification(
                    user_id=admin.id,
                    type="verification",
                    title="Supplier auto-verified",
                    message=f"Supplier '{vendor.business_name}' was auto-verified via CAC.",
                    read=False,
                ))
            await db.commit()

            logger.info(f"[auto-verify] vendor {vendor_id} auto-verified successfully")
    except Exception as e:
        logger.error(f"[auto-verify] unexpected error for vendor {vendor_id}: {e}")


### Onboard current user as a vendor
@router.post("/onboard", response_model=VendorResponse, status_code=status.HTTP_201_CREATED)
async def onboard_vendor(
    vendor_in: VendorCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    
    # Check if user already has a vendor profile
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    existing_vendor = result.scalar_one_or_none()
    
    # A Vendor row may already exist (auto-created at signup). We update it below
    # rather than rejecting, so the user can complete/fill in onboarding later.
    
    # Check for duplicate business registration number if provided
    if vendor_in.cac_business_registration_number:
        result = await db.execute(
            select(Vendor).where(
                Vendor.cac_business_registration_number == vendor_in.cac_business_registration_number
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Business registration number already exists"
            )
        
    from app.config import settings
    from app.models.vendor_bank_account import VendorBankAccount
    
    # Use default commission from config or category default
    default_commission = getattr(settings, 'DEFAULT_VENDOR_COMMISSION', 10.00)

    # --- Onboard fast: write the vendor immediately. No blocking CAC lookup
    # inside the request (so the user is never stuck). If a CAC result was
    # pre-fetched via /verify-business, we can verify instantly here; otherwise
    # the vendor starts as pending and auto-verification runs in the background.
    user_id_str = str(current_user.id)
    cac_data = _cac_cache.pop(user_id_str, None)  # Consume cached result (may be None)

    verified = False
    ver_status = "pending"

    # Always start as pending. The background task (_auto_verify_vendor_background)
    # will flip to verified when the CAC lookup succeeds and the TIN matches.
    # The instant-verify fast path has been removed so that the background task
    # runs in all environments and the vendor row transitions pending → verified.
    # if settings.DEBUG:
    #     ver_status = "verified"
    #     verified = True
    
    # onboard vendor
    if existing_vendor:
        # Auto-created at signup - update it with the submitted onboarding details.
        vendor = existing_vendor
        vendor.business_name = vendor_in.business_name
        vendor.business_type = vendor_in.business_type
        vendor.business_address = vendor_in.business_address
        vendor.city = vendor_in.city
        vendor.state = vendor_in.state
        vendor.cac_business_registration_number = vendor_in.cac_business_registration_number
        vendor.tax_identification_number = vendor_in.tax_identification_number
        vendor.verification_status = ver_status
        # Keep existing commission_rate (do not reset).
    else:
        vendor = Vendor(
            user_id=current_user.id,
            business_name=vendor_in.business_name,
            business_type=vendor_in.business_type,
            business_address=vendor_in.business_address,
            city=vendor_in.city,
            state=vendor_in.state,
            cac_business_registration_number=vendor_in.cac_business_registration_number,
            tax_identification_number=vendor_in.tax_identification_number,
            verification_status=ver_status,
            commission_rate=default_commission,
            verification_date=datetime.now(timezone.utc).replace(tzinfo=None) if verified else None,
        )
        db.add(vendor)
        await db.flush()  # Get vendor.id before creating bank account
    
    # Create or update the primary bank account
    existing_account = None
    if existing_vendor:
        existing_account = (await db.execute(
            select(VendorBankAccount).where(
                VendorBankAccount.vendor_id == vendor.id,
                VendorBankAccount.is_primary.is_(True),
            )
        )).scalar_one_or_none()
    if existing_account:
        existing_account.bank_name = vendor_in.bank_name
        existing_account.account_number = vendor_in.bank_account_number
        existing_account.account_name = vendor_in.bank_account_name
        existing_account.bank_code = vendor_in.bank_code if hasattr(vendor_in, 'bank_code') else None
    else:
        db.add(VendorBankAccount(
            vendor_id=vendor.id,
            bank_name=vendor_in.bank_name,
            account_number=vendor_in.bank_account_number,
            account_name=vendor_in.bank_account_name,
            bank_code=vendor_in.bank_code if hasattr(vendor_in, 'bank_code') else None,
            is_primary=True,
            verified=False,
        ))
    
    # Update user role to vendor after onboarding
    user_result = await db.execute(
        select(User).where(User.id == current_user.id)
    )
    user = user_result.scalar_one()
    user.role = "vendor"

    # Persist onboarding data that belongs to the User / UserProfile tables:
    #   phone_number  -> users.phone_number
    #   business_name -> user_profiles.business_name
    # These are committed in the same transaction as the vendor row below.
    if vendor_in.phone_number:
        user.phone_number = vendor_in.phone_number
    if vendor_in.business_name:
        profile = current_user.profile
        if profile is None:
            profile_result = await db.execute(
                select(UserProfile).where(UserProfile.user_id == current_user.id)
            )
            profile = profile_result.scalar_one_or_none()
        if profile:
            profile.business_name = vendor_in.business_name
        else:
            db.add(UserProfile(
                user_id=current_user.id,
                first_name=(current_user.email or "").split("@")[0] or "New",
                last_name="User",
                business_name=vendor_in.business_name,
            ))
    
    # Delete draft after successful onboarding
    draft_result = await db.execute(
        select(VendorDraft).where(VendorDraft.user_id == current_user.id)
    )
    draft = draft_result.scalar_one_or_none()
    if draft:
        await db.delete(draft)
    
    await db.commit()
    await db.refresh(vendor)
    
    logger.info(f"User {current_user.id} registered as vendor: {vendor.business_name}")
    
    # Notify admins of a new vendor application pending verification so it
    # surfaces in their review queue (works in both dev & production).
    if ver_status == "pending":
        try:
            admin_result = await db.execute(
                select(User).where(User.role.in_(["admin", "super_admin"]))
            )
            admins = admin_result.scalars().all()
            for admin in admins:
                db.add(Notification(
                    user_id=admin.id,
                    type="vendor_verification",
                    title="New supplier application awaiting verification",
                    message=f"New supplier '{vendor.business_name}' has submitted an application and needs verification.",
                    read=False,
                ))
            await db.commit()
        except Exception as e:
            logger.error(f"Failed to notify admins of new vendor {vendor.id}: {e}")
    
    # Auto-verify in the background so the user is never stuck on a
    # verification window. If the auto-check eventually passes, this task
    # flips the same vendor row from pending → verified (self-healing).
    if ver_status == "pending" and vendor_in.cac_business_registration_number and not settings.DEBUG:
        background_tasks.add_task(
            _auto_verify_vendor_background,
            vendor_id=str(vendor.id),
            rc_number=vendor_in.cac_business_registration_number,
            submitted_tin=vendor_in.tax_identification_number or "",
        )
    
    return vendor

### Get my vendor profile (includes bank account info)
@router.get("/me", response_model=VendorResponse)
async def get_my_vendor_profile(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Vendor)
        .options(selectinload(Vendor.bank_accounts))
        .where(Vendor.user_id == current_user.id)
    )
    vendor = result.scalar_one_or_none()
    
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found. Please register as a vendor first."
        )
    
    # Inject primary bank account fields into response for backward compatibility
    primary_account = next((a for a in vendor.bank_accounts if a.is_primary), None)
    if primary_account:
        vendor.bank_name = primary_account.bank_name
        vendor.bank_account_number = primary_account.account_number
        vendor.bank_account_name = primary_account.account_name
    
    return vendor

### Update my vendor profile
@router.put("/me", response_model=VendorResponse)
async def update_my_vendor_profile(
    vendor_in: VendorUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    vendor = result.scalar_one_or_none()
    
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found"
        )
    
    # Update only provided fields
    update_data = vendor_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(vendor, field, value)
    
    await db.commit()
    await db.refresh(vendor)
    
    return vendor

### Get vendor verification status (for dashboard banner)
@router.get("/me/status")
async def get_vendor_status(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    vendor = result.scalar_one_or_none()

    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found. Please register as a vendor first."
        )

    # Tier + cap metadata for the dashboard banner
    from app.models.vendor_verification_tier import VendorVerificationTier
    tiers_res = await db.execute(
        select(VendorVerificationTier)
        .where(VendorVerificationTier.is_active.is_(True))
        .order_by(VendorVerificationTier.sort_order)
    )
    tiers = tiers_res.scalars().all()
    current_tier = next((t for t in tiers if t.tier_code == vendor.verification_tier), None)
    trans_cap = float(current_tier.transaction_cap) if current_tier else 5_000_000.0
    volume = float(vendor.transaction_volume or 0)
    volume_pct = round((volume / trans_cap) * 100, 1) if trans_cap else 0

    current_rank = current_tier.sort_order if current_tier else 1
    next_tier = next((t for t in tiers if t.sort_order > current_rank), None)

    return {
        "verification_status": vendor.verification_status,
        "verification_date": vendor.verification_date.isoformat() if vendor.verification_date else None,
        "rating": float(vendor.rating) if vendor.rating else 0,
        "total_reviews": vendor.total_reviews or 0,
        "total_sales": float(vendor.total_sales) if vendor.total_sales else 0,
        "is_featured": vendor.is_featured or False,
        "tier": vendor.verification_tier,
        "tier_name": current_tier.display_name if current_tier else vendor.verification_tier,
        "transaction_cap": trans_cap,
        "transaction_volume": volume,
        "volume_pct": volume_pct,
        "next_tier": {
            "tier_code": next_tier.tier_code,
            "display_name": next_tier.display_name,
            "transaction_cap": float(next_tier.transaction_cap),
            "commission_rate": float(next_tier.commission_rate),
            "perks": next_tier.perks or [],
        } if next_tier else None,
    }

### Get demand alerts for vendor (products out of stock that customers want)
@router.get("/me/demand-alerts")
async def get_demand_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Get vendor first
    vendor_result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    vendor = vendor_result.scalar_one_or_none()

    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found"
        )

    # Fetch demand alerts matching vendor's city
    count_stmt = text("""
        SELECT COUNT(*) FROM demand_alerts
        WHERE city = :city AND status = 'pending'
    """)
    count_result = await db.execute(count_stmt, {"city": vendor.city})
    total = count_result.scalar() or 0

    data_stmt = text("""
        SELECT
            id, item_description, city, quantity_needed, unit,
            project_title, requested_by, status, created_at
        FROM demand_alerts
        WHERE city = :city AND status = 'pending'
        ORDER BY created_at DESC
        OFFSET :offset LIMIT :limit
    """)
    data_result = await db.execute(data_stmt, {
        "city": vendor.city,
        "offset": (page - 1) * page_size,
        "limit": page_size,
    })
    rows = data_result.fetchall()

    alerts = []
    for row in rows:
        requester_name = "Anonymous"
        if row.requested_by:
            user_result = await db.execute(
                select(User).where(User.id == row.requested_by)
            )
            requester = user_result.scalar_one_or_none()
            if requester and requester.profile:
                requester_name = requester.profile.first_name or requester.email.split("@")[0]

        alerts.append({
            "id": str(row.id),
            "item_description": row.item_description,
            "city": row.city,
            "quantity_needed": float(row.quantity_needed) if row.quantity_needed else None,
            "unit": row.unit,
            "project_title": row.project_title,
            "requested_by_name": requester_name,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    return {
        "alerts": alerts,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


### Save vendor draft
@router.put("/draft", response_model=VendorDraftResponse)
async def save_vendor_draft(
    draft_in: VendorDraftSave,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(VendorDraft).where(VendorDraft.user_id == current_user.id)
    )
    draft = result.scalar_one_or_none()

    if draft:
        if draft_in.current_step is not None:
            draft.current_step = draft_in.current_step
        if draft_in.business_info is not None:
            draft.business_info = draft_in.business_info
        if draft_in.banking_info is not None:
            draft.banking_info = draft_in.banking_info
    else:
        draft = VendorDraft(
            user_id=current_user.id,
            current_step=draft_in.current_step or "business-info",
            business_info=draft_in.business_info or {},
            banking_info=draft_in.banking_info or {},
        )
        db.add(draft)

    await db.commit()
    await db.refresh(draft)
    return draft


### Get vendor draft
@router.get("/draft", response_model=VendorDraftResponse)
async def get_vendor_draft(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(VendorDraft).where(VendorDraft.user_id == current_user.id)
    )
    draft = result.scalar_one_or_none()

    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No saved draft found"
        )

    return draft


### Delete vendor draft
@router.delete("/draft", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vendor_draft(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(VendorDraft).where(VendorDraft.user_id == current_user.id)
    )
    draft = result.scalar_one_or_none()

    if draft:
        await db.delete(draft)
        await db.commit()

### Deactivate vendor account
@router.put("/me/deactivate")
async def deactivate_vendor(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found"
        )

    vendor.verification_status = "deactivated"
    await db.commit()
    return {"message": "Account deactivated successfully", "verification_status": "deactivated"}


### Reactivate vendor account
@router.put("/me/reactivate")
async def reactivate_vendor(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found"
        )

    vendor.verification_status = "pending"
    await db.commit()
    return {"message": "Account reactivated. Pending re-verification.", "verification_status": "pending"}


### Upload vendor business image
@router.post("/me/image")
async def upload_vendor_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/jpg"}
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, and WebP images are allowed"
        )

    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found"
        )

    try:
        image_url = await upload_image_to_cloudinary(file)
        vendor.business_image = image_url
        await db.commit()
        await db.refresh(vendor)
        return {"url": image_url, "message": "Image uploaded successfully"}
    except Exception as e:
        logger.error(f"Failed to upload vendor image: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload image. Please try again."
        )


### Verify business via CAC — background pre-fetch endpoint
@router.post("/verify-business")
async def verify_business_cac(
    rc_number: str = Query(..., min_length=1, max_length=20, description="CAC RC number (digits only)"),
    current_user: dict = Depends(get_current_user),
):
    from app.tasks.cac_tasks import get_cac_business_info

    user_id_str = str(current_user.id)

    result: dict = {}
    for attempt in range(2):
        result = await asyncio.to_thread(get_cac_business_info, rc_number)
        logger.info(f"CAC lookup attempt {attempt + 1} for RC={rc_number}: complete={_is_cac_result_complete(result)}")
        if _is_cac_result_complete(result):
            break
        if attempt == 0:
            # Brief delay before retry
            await asyncio.sleep(1.5)

    # Cache result for later use in /onboard
    _cac_cache[user_id_str] = result

    return {
        "rc_number": rc_number,
        "cac_data": result,
        "complete": _is_cac_result_complete(result),
    }