"""Driver schemas (Phase 2 - driver self-onboarding & identity API)."""
from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime


class DriverProfileOut(BaseModel):
    id: UUID
    user_id: UUID
    vendor_id: Optional[UUID] = None

    source: str            # self | vendor
    status: str            # pending | active | suspended
    full_name: Optional[str] = None
    phone: Optional[str] = None

    availability: bool     # online/offline toggle

    vehicle_type: Optional[str] = None
    vehicle_plate: Optional[str] = None
    vehicle_capacity: Optional[str] = None
    license_no: Optional[str] = None

    default_address: Optional[str] = None
    default_city: Optional[str] = None
    default_state: Optional[str] = None
    default_lat: Optional[float] = None
    default_lng: Optional[float] = None

    # Aggregate rating (Phase 1B)
    rating: Optional[float] = None
    rating_count: Optional[int] = None

    # Payout / settlement account - the single bank account earnings are paid
    # into. Nullable until the driver supplies them.
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_code: Optional[str] = None

    # Convenience flag for the UI nudge (true once a complete account is saved).
    has_bank_details: bool = False

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DriverOnboardingUpdate(BaseModel):
    """Profile + vehicle details the driver fills in during/after onboarding."""

    full_name: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    vehicle_type: Optional[str] = Field(None, max_length=100)
    vehicle_plate: Optional[str] = Field(None, max_length=50)
    vehicle_capacity: Optional[str] = Field(None, max_length=100)
    license_no: Optional[str] = Field(None, max_length=100)


class DriverAvailabilityUpdate(BaseModel):
    availability: bool


class DriverBankDetailsUpdate(BaseModel):
    """The single bank account a driver's marketplace earnings are settled into.

    All three mandatory fields are supplied together (the same rule the vendor
    payout-account upsert enforces in `vendors.py`): a half-filled account is
    worse than none, because the payout runner would resolve it and only fail
    later at the provider.

    Rules: NG NUBAN account numbers are exactly 10 digits; the optional
    `bank_code` feeds gateways that need it and is rejected if it isn't numeric.
    """

    bank_name: str = Field(..., min_length=2, max_length=100)
    bank_account_number: str = Field(..., pattern=r"^\d{10}$")
    bank_account_name: str = Field(..., min_length=2, max_length=255)
    bank_code: Optional[str] = Field(None, pattern=r"^\d{1,10}$")


class DriverDefaultLocationUpdate(BaseModel):
    """Default / operating location (city+state for MVP; coords kept for later)."""

    address: Optional[str] = Field(None, max_length=500)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)