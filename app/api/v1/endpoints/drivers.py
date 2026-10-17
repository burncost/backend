"""Driver identity, availability & default-location API (Phase 2).

Self-onboarded drivers register via /auth/register with role=driver (which also
auto-creates their DriverProfile). They then use these endpoints to complete
their profile (vehicle/license), upload the vehicle photo set (front, back and
both sides), set their default / operating location, and toggle availability
online/offline.

Live location streaming is added in a later phase; the default-location
coordinates stored here are used for nearby matching.

Onboarding gate: a driver may only go online / accept delivery jobs once all
four vehicle photos are on file. The rule lives on the model
(`vehicle_photos_complete`) so the API and the driver UI read the same truth.
"""
from datetime import datetime
from decimal import Decimal
from typing import List
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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
from app.utils.image_processing import (
    ImageTooLarge,
    UnsupportedImage,
    normalize_image,
)
from app.utils.storage import StorageService

router = APIRouter()

# angle -> DriverProfile column holding that photo's URL.
_VEHICLE_PHOTO_COLUMNS = {
    "front": "vehicle_photo_front",
    "back": "vehicle_photo_back",
    "left": "vehicle_photo_left",
    "right": "vehicle_photo_right",
}


def _validate_angle(angle: str) -> str:
    normalized = (angle or "").strip().lower()
    if normalized not in _VEHICLE_PHOTO_COLUMNS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid photo angle '{angle}'. Use one of: "
            + ", ".join(settings.VEHICLE_PHOTO_ANGLES),
        )
    return normalized



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
    # Going online is the point a driver starts receiving jobs, so the vehicle
    # photo requirement is enforced here as well as on accept (defence in depth:
    # the accept endpoint remains authoritative for direct assignments).
    if payload.availability and not profile.vehicle_photos_complete:
        missing = ", ".join(profile.missing_vehicle_photos)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Upload your vehicle photos before going online. Missing: {missing}.",
        )
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
    together (enforced by the schema), so this is always a full upsert - there is
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


@router.post("/me/vehicle-photos", response_model=DriverProfileOut)
async def upload_vehicle_photos(
    files: List[UploadFile] = File(...),
    angles: List[str] = Form(...),
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Upload (or replace) the vehicle onboarding photos.

    Accepts several images in one multipart request - the driver picks all four
    shots in the file picker and each file is paired with its angle via the
    parallel ``angles`` form field (``front`` / ``back`` / ``left`` / ``right``).

    Every image is normalised before it is stored: EXIF auto-orientation,
    longest edge capped at ``VEHICLE_PHOTO_MAX_EDGE`` and re-encoded until the
    stored JPEG is at most ``VEHICLE_PHOTO_MAX_BYTES`` (0.5 MB). The bytes that
    reach storage therefore never exceed the budget, regardless of what the
    driver's camera produced.

    All four angles are required by the delivery gate, but this endpoint allows
    a partial set (1-4 files) so a driver can resume onboarding later.
    """
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No files uploaded.")
    if len(files) != len(angles):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Each file must be paired with exactly one angle (front/back/left/right).",
        )
    if len(files) > len(_VEHICLE_PHOTO_COLUMNS):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At most 4 vehicle photos can be uploaded at once.",
        )

    normalized_angles = [_validate_angle(a) for a in angles]
    if len(set(normalized_angles)) != len(normalized_angles):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Each angle may appear only once per upload.",
        )

    storage = StorageService()
    folder = f"driver-vehicles/{profile.id}"
    uploaded_urls: List[str] = []
    replaced_urls: List[str] = []

    try:
        for upload, angle in zip(files, normalized_angles):
            column = _VEHICLE_PHOTO_COLUMNS[angle]
            previous_url = getattr(profile, column)
            content = await upload.read(settings.VEHICLE_PHOTO_MAX_UPLOAD_BYTES + 1)
            if len(content) > settings.VEHICLE_PHOTO_MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        f"{upload.filename or angle}: file is larger than "
                        f"{settings.VEHICLE_PHOTO_MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                    ),
                )

            try:
                normalized = normalize_image(content)
            except UnsupportedImage as exc:
                raise HTTPException(
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail=f"{upload.filename or angle}: {exc}",
                ) from exc
            except ImageTooLarge as exc:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"{upload.filename or angle}: {exc}",
                ) from exc

            url = await storage.upload_file(
                file=normalized,
                folder=folder,
                filename=f"{angle}-{uuid4().hex}.jpg",
            )
            uploaded_urls.append(url)
            setattr(profile, column, url)
            if previous_url:
                replaced_urls.append(previous_url)

        profile.vehicle_photos_updated_at = datetime.utcnow()
        profile.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(profile)
        # The old images are only removed once the new URLs are committed, so a
        # failed upload can never leave a driver with no photo for an angle.
        for url in replaced_urls:
            try:
                await storage.delete_file(url)
            except Exception:  # best-effort cleanup only
                pass
        return profile

    except Exception:
        # Never leave orphaned objects (or a half-applied profile) behind when a
        # later file in the batch fails validation.
        await db.rollback()
        for url in uploaded_urls:
            try:
                await storage.delete_file(url)
            except Exception:  # best-effort cleanup only
                pass
        raise


@router.delete("/me/vehicle-photos/{angle}", response_model=DriverProfileOut)
async def delete_vehicle_photo(
    angle: str,
    profile: DriverProfile = Depends(get_current_driver_profile),
    db: AsyncSession = Depends(get_db),
):
    """Remove one vehicle photo (so it can be replaced with a better shot).

    Removing a photo makes the photo set incomplete and therefore blocks going
    online / accepting jobs until it is uploaded again.
    """
    normalized = _validate_angle(angle)
    column = _VEHICLE_PHOTO_COLUMNS[normalized]
    removed_url = getattr(profile, column)
    setattr(profile, column, None)
    profile.vehicle_photos_updated_at = datetime.utcnow()
    profile.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(profile)
    if removed_url:
        try:
            await StorageService().delete_file(removed_url)
        except Exception:  # best-effort: the DB row is already the source of truth
            pass
    return profile
