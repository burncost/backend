from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.schemas.user import UserResponse, UserUpdate
from app.crud import user as user_crud
from app.api.deps import get_current_user
from app.models.user import UserProfile

router = APIRouter()

### Get current user
@router.get("/me", response_model=UserResponse)
async def read_users_me(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = await user_crud.get(db, id=current_user["id"])
    return user


### Update current user profile
@router.put("/me", response_model=UserResponse)
async def update_user_me(
    user_in: UserUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = await user_crud.get(db, id=current_user["id"])
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
    profile_model_fields = {'first_name', 'last_name', 'business_name', 'avatar_url'}
    
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