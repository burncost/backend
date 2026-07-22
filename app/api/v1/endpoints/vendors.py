from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from uuid import UUID
from typing import Optional

from app.core.database import get_db
from app.models.vendor import Vendor
from app.models.user import User
from app.models.notification import Notification
from app.models.vendor_draft import VendorDraft
from app.schemas.vendor import VendorCreate, VendorUpdate, VendorResponse
from app.services.cloudinary_upload_service import upload_image_to_cloudinary
from app.schemas.vendor_draft import VendorDraftSave, VendorDraftResponse
from app.api.deps import get_current_user
from app.services.auth_service import AuthService
import logging

router = APIRouter()
logger = logging.getLogger(__name__)
auth_service = AuthService()

### Onboard current user as a vendor
@router.post("/onboard", response_model=VendorResponse, status_code=status.HTTP_201_CREATED)
async def onboard_vendor(
    vendor_in: VendorCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    
    # Check if user already has a vendor profile
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    existing_vendor = result.scalar_one_or_none()
    
    if existing_vendor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already registered as vendor"
        )
    
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
    
    # Dev: auto-verify for testing; Prod: require admin approval
    ver_status = "verified" if settings.DEBUG else "pending"
    
    from app.models.vendor_bank_account import VendorBankAccount
    
    # Use default commission from config or category default
    default_commission = getattr(settings, 'DEFAULT_VENDOR_COMMISSION', 10.00)
    
    # onboard vendor
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
        commission_rate=default_commission
    )
    db.add(vendor)
    await db.flush()  # Get vendor.id before creating bank account
    
    # Create primary bank account
    bank_account = VendorBankAccount(
        vendor_id=vendor.id,
        bank_name=vendor_in.bank_name,
        account_number=vendor_in.bank_account_number,
        account_name=vendor_in.bank_account_name,
        bank_code=vendor_in.bank_code if hasattr(vendor_in, 'bank_code') else None,
        is_primary=True,
        verified=False,
    )
    db.add(bank_account)
    
    # Update user role to vendor after onboarding
    user_result = await db.execute(
        select(User).where(User.id == current_user.id)
    )
    user = user_result.scalar_one()
    user.role = "vendor"
    
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

    return {
        "verification_status": vendor.verification_status,
        "verification_date": vendor.verification_date.isoformat() if vendor.verification_date else None,
        "rating": float(vendor.rating) if vendor.rating else 0,
        "total_reviews": vendor.total_reviews or 0,
        "total_sales": float(vendor.total_sales) if vendor.total_sales else 0,
        "is_featured": vendor.is_featured or False,
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
