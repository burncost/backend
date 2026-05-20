from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.core.database import get_db
from app.models.vendor import Vendor
from app.models.user import User
from app.schemas.vendor import VendorCreate, VendorResponse
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
        select(Vendor).where(Vendor.user_id == current_user["id"])
    )
    existing_vendor = result.scalar_one_or_none()
    
    if existing_vendor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already registered as vendor"
        )
    
    # Check for duplicate business registration number if provided
    if vendor_in.business_registration_number:
        result = await db.execute(
            select(Vendor).where(
                Vendor.business_registration_number == vendor_in.business_registration_number
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Business registration number already exists"
            )
        
    # verify_business_result = await auth_service.verify_business(vendor_in.cac_business_registration_number)
    
    # if not verify_business_result:
    #     raise HTTPException(
    #         status_code=status.HTTP_400_BAD_REQUEST,
    #         detail="CAC verification failed. Please upload your credentials for manual review."
    #     )
    
    # # logger.info(f"\n==========\nverify_business_result: {verify_business_result}")
    # cac_rc_number = verify_business_result.get("rc_number")==vendor_in.cac_business_registration_number if verify_business_result else None
    # cac_busess_name = verify_business_result.get("business_name").lower()==vendor_in.business_name.lower() if verify_business_result else None
    # cac_status = verify_business_result.get("status").lower()=="active" if verify_business_result else None

    # # logger.info(f"====\n\n\n===={cac_rc_number}\n{cac_busess_name}\n{cac_status}\n{verify_business_result.get("tax_id")}\n====")
    # if cac_rc_number and cac_busess_name and cac_status:
    #     ver_status = "verified"
    # else:
    #     ver_status = "pending"
    
    ver_status = "pending"
    
    # onboard vendor
    vendor = Vendor(
        user_id=UUID(current_user["id"]),
        business_name=vendor_in.business_name,
        business_type=vendor_in.business_type,
        business_address=vendor_in.business_address,
        city=vendor_in.city,
        state=vendor_in.state,
        cac_business_registration_number=vendor_in.cac_business_registration_number,
        tax_identification_number=vendor_in.tax_identification_number,
        verification_status=ver_status,
        bank_account_name=vendor_in.bank_account_name,
        bank_account_number=vendor_in.bank_account_number,
        bank_name=vendor_in.bank_name,
        commission_rate=0.00
    )
    db.add(vendor)
    
    # Update user role to vendor
    # user_result = await db.execute(
    #     select(User).where(User.id == current_user["id"])
    # )
    # user = user_result.scalar_one()
    # user.role = "vendor"
    
    await db.commit()
    await db.refresh(vendor)
    
    logger.info(f"User {current_user['id']} registered as vendor: {vendor.business_name}")
    
    return vendor

### Get my vendor profile
@router.get("/me", response_model=VendorResponse)
async def get_my_vendor_profile(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user["id"])
    )
    vendor = result.scalar_one_or_none()
    
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found. Please register as a vendor first."
        )
    
    return vendor

### Update my vendor profile
@router.put("/me", response_model=VendorResponse)
async def update_my_vendor_profile(
    vendor_in: VendorCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user["id"])
    )
    vendor = result.scalar_one_or_none()
    
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found"
        )
    
    # Update fields
    vendor.business_name = vendor_in.business_name
    if vendor_in.business_registration_number:
        vendor.business_registration_number = vendor_in.business_registration_number
    if vendor_in.tax_identification_number:
        vendor.tax_identification_number = vendor_in.tax_identification_number
    
    await db.commit()
    await db.refresh(vendor)
    
    return vendor