from typing import Optional, Any
from sqlalchemy import select, func
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

    ###Get user by email (case-insensitive, so `A@x.com` == `a@x.com`)
    async def get_by_email(self, db: AsyncSession, *, email: str) -> Optional[User]:
        result = await db.execute(
            select(User)
            .options(selectinload(User.profile))
            .where(func.lower(User.email) == (email or "").strip().lower())
            .order_by(User.created_at.asc())
            .limit(1)
        )
        return result.scalars().first()

    ### Get user by phone number (format-insensitive: compares last 10 digits,
    ### so `0803…` and `+234803…` are treated as the same number)
    async def get_by_phone(self, db: AsyncSession, *, phone: str) -> Optional[User]:
        digits = "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]
        if not digits:
            return None
        phone_digits = func.regexp_replace(func.coalesce(User.phone_number, ""), r"[^0-9]", "", "g")
        result = await db.execute(
            select(User)
            .options(selectinload(User.profile))
            .where(func.right(phone_digits, 10) == digits)
            .order_by(User.created_at.asc())
            .limit(1)
        )
        return result.scalars().first()

    ### Create new user with hashed password and profile
    async def create(self, db: AsyncSession, *, obj_in: UserCreate) -> User:
        # Convert string role to enum member if needed
        from app.models.user import UserRole
        role_value = obj_in.role
        if isinstance(role_value, str):
            matched = None
            for member in UserRole:
                if member.value == role_value:
                    matched = member
                    break
            if matched is None:
                # Strict: an unrecognized role must never be written (no defaults).
                raise ValueError(f"Invalid role: {obj_in.role!r}")
            role_value = matched
        elif role_value is None:
            raise ValueError("Role is required")
        
        db_obj = User(
            email=obj_in.email,
            phone_number=obj_in.phone_number,
            password_hash=get_password_hash(obj_in.password),
            role=role_value,
            status="active",
            password_changed_at=datetime.utcnow(),
        )
        db.add(db_obj)
        try:
            await db.commit()
            await db.refresh(db_obj)

            # Create user profile (missing optional fields get safe defaults)
            profile = UserProfile(
                user_id=db_obj.id,
                first_name=(obj_in.first_name or "").strip() or "New",
                other_name=obj_in.other_name,
                last_name=(obj_in.last_name or "").strip() or "User",
                business_name=obj_in.business_name,
                location=obj_in.location or "",
            )
            db.add(profile)
            await db.commit()
        except Exception:
            # Registration failed partway (e.g. profile insert failed after the
            # user row was committed) — roll back and remove any partial user
            # row so nothing incomplete is left in the database.
            await db.rollback()
            await db.execute(
                User.__table__.delete().where(User.id == db_obj.id)
            )
            await db.commit()
            raise
        
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
            # Reset the password age so `password_expiration` starts counting again.
            user.password_changed_at = datetime.utcnow()
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user


user = CRUDUser(User)
