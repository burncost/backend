"""Driver identity, availability & default-location API (Phase 2).

Self-onboarded drivers register via /auth/register with role=driver (which also
auto-creates their DriverProfile). They then use these endpoints to complete
their profile (vehicle/license), set their default / operating location, and
toggle availability online/offline.

Live location streaming is added in a later phase; the default-location
coordinates stored here are used for nearby matching.
"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_driver_profile
from app.models.driver import DriverProfile
from app.schemas.driver import (
    DriverAvailabilityUpdate,
    DriverBankDetailsUpdate,
    DriverDefaultLocationUpdate,
    DriverOnboardingUpdate,
    DriverProfileOut,
)

router = APIRouter()


@router.get("/me", response_model=DriverProfileOut)
async def get_driver_profile(
    profile: DriverProfile = Depends(get_current_driver_profile),
):
    """Return the authenticated driver's profile."""
    return profile


@router.patch("/me", response_model=DriverProfileOut)
async def update_driver_profile(
    payload: DriverOnboardingUpdate,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Complete/update driver profile and vehicle/licence details."""
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(profile, field, value)
    profile.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(profile)
    return profile


@router.patch("/me/availability", response_model=DriverProfileOut)
async def set_driver_availability(
    payload: DriverAvailabilityUpdate,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Toggle the driver online/offline to receive & accept delivery offers."""
    profile.availability = payload.availability
    profile.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(profile)
    return profile


@router.patch("/me/bank-details", response_model=DriverProfileOut)
async def set_driver_bank_details(
    payload: DriverBankDetailsUpdate,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Create or update the driver's payout / settlement account.

    Drives the earnings surface: marketplace earnings accrue against the ledger
    and are disbursed to this account. The three mandatory fields are required
    together (enforced by the schema), so this is always a full upsert — there is
    no way to leave a half-filled account behind for the payout runner to trip on.
    """
    profile.bank_name = payload.bank_name.strip()
    profile.bank_account_number = payload.bank_account_number.strip()
    profile.bank_account_name = payload.bank_account_name.strip()
    # Optional: only overwrite when supplied, so a driver editing an existing
    # account is not silently stripped of a bank code they saved earlier.
    if payload.bank_code is not None:
        profile.bank_code = payload.bank_code.strip() or None
    profile.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(profile)
    return profile


@router.patch("/me/location", response_model=DriverProfileOut)
async def set_driver_default_location(
    payload: DriverDefaultLocationUpdate,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Set the driver's default / operating location (city + coords)."""
    if payload.address is not None:
        profile.default_address = payload.address
    if payload.city is not None:
        profile.default_city = payload.city
    if payload.state is not None:
        profile.default_state = payload.state
    if payload.lat is not None:
        profile.default_lat = Decimal(str(payload.lat))
    if payload.lng is not None:
        profile.default_lng = Decimal(str(payload.lng))
    profile.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(profile)
    return profile