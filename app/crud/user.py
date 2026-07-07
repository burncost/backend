from typing import Optional, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from datetime import datetime

from app.crud.base import CRUDBase
from app.models.user import User, UserProfile
from app.schemas.user import UserCreate, UserUpdate
from app.core.security import get_password_hash

## Get user by ID with profile loaded
class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):
    async def get(self, db: AsyncSession, id: Any) -> Optional[User]:
        result = await db.execute(
            select(User)
            .options(selectinload(User.profile))
            .where(User.id == id)
        )
        return result.scalar_one_or_none()

    ###Get user by email
    async def get_by_email(self, db: AsyncSession, *, email: str) -> Optional[User]:
        result = await db.execute(
            select(User)
            .options(selectinload(User.profile))
            .where(User.email == email)
        )
        return result.scalar_one_or_none()

    ### Get user by phone number
    async def get_by_phone(self, db: AsyncSession, *, phone: str) -> Optional[User]:
        result = await db.execute(
            select(User)
            .options(selectinload(User.profile))
            .where(User.phone_number == phone)
        )
        return result.scalar_one_or_none()

    ### Create new user with hashed password and profile
    async def create(self, db: AsyncSession, *, obj_in: UserCreate) -> User:
        # Convert string role to enum member if needed
        from app.models.user import UserRole
        role_value = obj_in.role
        if isinstance(role_value, str):
            for member in UserRole:
                if member.value == role_value:
                    role_value = member
                    break
        
        db_obj = User(
            email=obj_in.email,
            phone_number=obj_in.phone_number,
            password_hash=get_password_hash(obj_in.password),
            role=role_value,
            status="pending_verification",
            # status="active",
        )
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        
        # Create user profile
        profile = UserProfile(
            user_id=db_obj.id,
            first_name=obj_in.first_name,
            last_name=obj_in.last_name,
            business_name=obj_in.business_name,
        )
        db.add(profile)
        await db.commit()
        
        # Reload user with profile
        result = await db.execute(
            select(User)
            .options(selectinload(User.profile))
            .where(User.id == db_obj.id)
        )
        return result.scalar_one()

    ### Update user's last login timestamp
    async def update_last_login(self, db: AsyncSession, *, user_id: Any) -> User:
        user = await self.get(db, id=user_id)
        if user:
            user.last_login = datetime.utcnow()
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user

    ### Mark user's email as verified
    async def verify_email(self, db: AsyncSession, *, user_id: Any) -> User:
        user = await self.get(db, id=user_id)
        if user:
            user.email_verified = True
            user.status = "active"
            # if user.phone_verified:
            #     user.status = "active"
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user

    ### Mark user's phone as verified
    async def verify_phone(self, db: AsyncSession, *, user_id: Any) -> User:
        user = await self.get(db, id=user_id)
        if user:
            user.phone_verified = True
            if user.email_verified:
                user.status = "active"
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user

    ### Update user's password
    async def update_password(self, db: AsyncSession, *, user_id: Any, new_password: str) -> User:
        from app.core.security import get_password_hash
        user = await self.get(db, id=user_id)
        if user:
            user.password_hash = get_password_hash(new_password)
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user


user = CRUDUser(User)
