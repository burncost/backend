from typing import Annotated, Generator, Optional
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import get_current_user_id, get_current_user_id_optional, decode_token

from app.crud import user as user_crud
from app.crud import vendor as vendor_crud
from app.models.vendor import Vendor
from app.models.user import User
from app.models.driver import DriverProfile
from app.services import security_settings

### Get current authenticated user (raises 401 if not authenticated)
async def get_current_user(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id)
):
    user = await user_crud.get(db, id=user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user


### Get current user or None (allows anonymous access)
async def get_optional_user(
    db: AsyncSession = Depends(get_db),
    user_id: Optional[str] = Depends(get_current_user_id_optional)
):
    if not user_id:
        return None
    user = await user_crud.get(db, id=user_id)
    return user


def _bearer_token(request: Request) -> Optional[str]:
    """Access token from the httpOnly cookie, else the Authorization header."""
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
    return token


async def enforce_admin_access(
    request: Request,
    current_user = Depends(get_current_user),
):
    """Apply the Admin → Settings → Security switches to admin-tier sessions.

    Wired underneath every guard in this module (and therefore every /admin/*
    route), so each switch actually has teeth:
      * IP allowlist          -> 403 code="ip_not_allowed"
      * 2FA requirement       -> 403 code="two_factor_required"
      * password expiry       -> 403 code="password_expired"
      * idle session timeout  -> 401 code="session_idle_timeout"

    Non-admin roles return immediately, so buyer/vendor/driver flows are
    completely unaffected. `/auth/2fa/*` and `/auth/logout` do not use these
    guards, which is what keeps enrolment reachable for an un-enrolled admin.
    """
    role_value = getattr(current_user.role, "value", current_user.role)
    if str(role_value) not in security_settings.ADMIN_ROLES:
        return current_user

    if not security_settings.ip_allowed(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ip_not_allowed",
                "message": "Your network is not permitted to access the admin console.",
            },
        )

    if security_settings.two_factor_enabled():
        payload: dict = {}
        token = _bearer_token(request)
        if token:
            try:
                payload = decode_token(token)
            except HTTPException:
                payload = {}
        # Both halves matter: an un-enrolled admin must never be waved through,
        # and a token minted before the toggle was switched on carries no claim.
        if not payload.get("tfa", False) or not getattr(current_user, "two_factor_method", None):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "two_factor_required",
                    "message": "Two-factor authentication is required. Please sign in again and enrol a factor.",
                },
            )

    if security_settings.is_password_expired(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "password_expired",
                "message": "Your password has expired. Please change it to continue.",
            },
        )

    await security_settings.enforce_session_idle(str(current_user.id))
    return current_user

### Get current active user
async def get_current_active_user(
    current_user = Depends(get_current_user)
):
    # Add additional checks if needed
    return current_user

### Get current vendor (must be a vendor user)
async def get_current_vendor(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    # Check if user role is vendor
    if current_user.role not in ["vendor", "admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a vendor. Please register as a vendor first."
        )
    
    # Get vendor profile
    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    vendor = result.scalar_one_or_none()
    
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found. Please complete vendor registration."
        )
    
    return {"id": str(vendor.id), "user_id": str(current_user.id), "business_name": vendor.business_name}


### Get current vendor that is verified (admins always allowed; blocks
### transaction endpoints for unverified suppliers so they can upload and
### explore but not transact until approved).
async def get_current_verified_vendor(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    if current_user.role not in ["vendor", "admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a vendor. Please register as a vendor first."
        )

    # Admins bypass the verification gate
    if current_user.role in ["admin", "super_admin"]:
        result = await db.execute(
            select(Vendor).where(Vendor.user_id == current_user.id)
        )
        vendor = result.scalar_one_or_none()
        if not vendor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor profile not found. Please complete vendor registration."
            )
        return {"id": str(vendor.id), "user_id": str(current_user.id), "business_name": vendor.business_name}

    result = await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor profile not found. Please complete vendor registration."
        )
    if (vendor.verification_status.value if hasattr(vendor.verification_status, "value") else str(vendor.verification_status or "")) in ("suspended", "deactivated", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not active for selling. Please contact support."
        )
    return {"id": str(vendor.id), "user_id": str(current_user.id), "business_name": vendor.business_name}


### Get current admin user
async def get_current_admin(
    current_user = Depends(enforce_admin_access)
):
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not an admin"
        )
    return current_user


### RBAC guard factory - require one of the given roles (admin & super_admin always allowed)
def require_roles(*roles: str):
    allowed = set(roles) | {"admin", "super_admin"}

    async def role_checker(
        current_user = Depends(enforce_admin_access)
    ):
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for this action"
            )
        return current_user

    return role_checker



### Get current driver profile (must be a driver-role user)
async def get_current_driver_profile(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("driver")),
):
    result = await db.execute(
        select(DriverProfile).where(DriverProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Driver profile not found. Please register as a driver or complete onboarding."
        )
    return profile


# ── Dependency shapes (read this before adding a `current_user` parameter) ────
# Two different shapes circulate under the same name, which caused real 500s
# (`current_user["id"]` on an ORM object -> TypeError: not subscriptable):
#
#   get_current_user / get_current_active_user / get_current_admin / require_roles(...)
#       -> SQLAlchemy `User` ORM instance. Annotate `current_user: User = Depends(...)`
#          and use attributes:   current_user.id, current_user.role
#   get_current_vendor / get_current_verified_vendor
#       -> plain dict {"id", "user_id", "business_name"}. Annotate `: dict = Depends(...)`
#          and use `current_vendor["id"]` / `.get(...)`. Note it has NO "role" key.
#
# Annotated[...] aliases are deliberately NOT used here: every parameter in this codebase
# carries a default (Query(...)/Depends(...)), and an Annotated dependency has none, so
# mixing them raises "non-default argument follows default argument".
#
# Also note: `app/deps.py` is a DIFFERENT module whose own `get_current_user` returns a
# dict — always import the dependency you actually mean.
