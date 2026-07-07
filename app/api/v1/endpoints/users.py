from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.schemas.user import UserResponse, UserUpdate
from app.crud import user as user_crud
from app.api.deps import get_current_user
from app.models.user import User, UserProfile
from app.models.address import CustomerAddress
from app.schemas.addresses import AddressUpdate

router = APIRouter()

### Get current user
@router.get("/me", response_model=UserResponse)
async def read_users_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = await user_crud.get(db, id=current_user.id)
    return user

@router.get("/preload-me")
async def read_users_preload_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = await user_crud.get(db, id=current_user.id)
    return {"email": user.email, 
            "phone_number": user.phone_number, 
            "business_name": user.profile.business_name}

@router.get("/client-me")
async def read_client_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = await user_crud.get(db, id=current_user.id)

    result = await db.execute(
            select(CustomerAddress).where(CustomerAddress.user_id == user.id)
        )
    address = result.scalar_one_or_none()

    return {"email": user.email, 
            "phone_number": user.phone_number, 
            "business_name": user.profile.business_name,
            "location": user.profile.location,
            "last_name": user.profile.last_name,
            "first_name": user.profile.first_name,
            "other_name": user.profile.other_name,
            "address_type": address.address_type.upper() if address else None,
            "contact_name": address.contact_name if address else None,
            "contact_phone": address.contact_phone if address else None,
            "address_line1": address.address_line1 if address else None,
            "address_line2": address.address_line2 if address else None,
            "city": address.city if address else None,
            "state": address.state if address else None,
            "lga": address.lga if address else None,
            "postal_code": address.postal_code if address else None,
            "landmark": address.landmark if address else None,
    }

### Update current user profile
@router.put("/me", response_model=UserResponse)
async def update_user_me(
    user_in: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = await user_crud.get(db, id=current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Separate user fields from profile fields
    user_fields = {}
    profile_fields = {}
    
    update_data = user_in.dict(exclude_unset=True)
    
    # Fields that belong to User model
    user_model_fields = {'email', 'phone_number'}
    # Fields that belong to UserProfile model
    profile_model_fields = {'first_name', 'last_name','other_name', 'business_name', 'avatar_url'}
    
    for field, value in update_data.items():
        if field in user_model_fields:
            user_fields[field] = value
        elif field in profile_model_fields:
            profile_fields[field] = value
    
    # Update User model if there are user fields
    if user_fields:
        ### Check for duplicate email/phone if being updated
        if 'email' in user_fields:
            existing = await user_crud.get_by_email(db, email=user_fields['email'])
            if existing and existing.id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already in use"
                )
        
        if 'phone_number' in user_fields:
            existing = await user_crud.get_by_phone(db, phone=user_fields['phone_number'])
            if existing and existing.id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Phone number already in use"
                )
        
        # Update user fields
        for field, value in user_fields.items():
            setattr(user, field, value)
    
    # Update UserProfile model if there are profile fields
    if profile_fields:
        # Get or create user profile
        result = await db.execute(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )
        profile = result.scalar_one_or_none()
        
        if not profile:
            # Create profile if it doesn't exist
            profile = UserProfile(
                user_id=user.id,
                first_name=profile_fields.get('first_name', ''),
                last_name=profile_fields.get('last_name', ''),
                other_name=profile_fields.get('other_name', ''),
                business_name=profile_fields.get('business_name'),
                avatar_url=profile_fields.get('avatar_url')
            )
            db.add(profile)
        else:
            # Update existing profile
            for field, value in profile_fields.items():
                setattr(profile, field, value)
    
    # Commit all changes
    await db.commit()
    await db.refresh(user)
    
    return user

### Update current user profile
@router.put("/me/address")
async def update_user_address(
    address_in: AddressUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = await user_crud.get(db, id=current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    address_data = address_in.dict(exclude_unset=True)

    try:
        result = await db.execute(
            select(CustomerAddress).where(CustomerAddress.user_id == user.id)
        )
        address = result.scalar_one_or_none()

        if not address:
            # Create address if it doesn't exist
            address = CustomerAddress(
                user_id=user.id,
                address_type=address_data.get('address_type'),
                contact_name=address_data.get('contact_name'),
                contact_phone=address_data.get('contact_phone'),
                address_line1=address_data.get('address_line1'),
                address_line2=address_data.get('address_line2'),
                city=address_data.get('city'),
                state=address_data.get('state'),
                lga=address_data.get('lga'),
                postal_code=address_data.get('postal_code'),
                landmark=address_data.get('landmark')
            )
            db.add(address)
        else:
            # Update existing address
            for field, value in address_data.items():
                setattr(address, field, value)
        
        await db.flush()
        await db.commit()
        await db.refresh(address)

        return {"success": True, "message": "Address updated successfully"}
    
    except ValueError as e:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update address: {str(e)}"
        )