"""Mono identity verification service (NIN + CAC lookup).

Used for vendor tier verification:
- NIN  -> verified_vendor: first/last name + date of birth must match.
- CAC  -> enterprise:      business name must match and status must be "active".

Lookup results are cached on user_profile.nin_verification_data (NIN) and
vendors.cac_verification_data (CAC) so repeat checks don't hit Mono again.
"""
import logging
from datetime import date, datetime
from typing import Any, Optional

import httpx

from app.config import settings
from app.models.user import UserProfile

logger = logging.getLogger(__name__)

MONO_BASE_URL = "https://api.withmono.com"


async def _mono_post(path: str, payload: dict, secret_key: str) -> Optional[dict]:
    headers = {"Content-Type": "application/json", "mono-sec-key": secret_key}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{MONO_BASE_URL}{path}", json=payload, headers=headers)
        if resp.status_code != 200:
            logger.warning("Mono lookup %s failed: %s %s", path, resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        if data.get("status") != "successful":
            logger.warning("Mono lookup %s unsuccessful: %s", path, data.get("message"))
            return None
        return data.get("data") or {}
    except Exception as e:
        logger.error("Mono lookup %s error: %s", path, e)
        return None


def _mono_enabled() -> bool:
    return bool(settings.MONO_SECRET_KEY)


async def verify_nin(nin: str) -> Optional[dict]:
    """Call Mono NIN lookup. Returns the raw `data` payload or None."""
    if not _mono_enabled() or not (nin or "").strip():
        return None
    return await _mono_post("/v3/lookup/nin", {"nin": nin.strip()}, settings.MONO_SECRET_KEY)


async def verify_cac(rc_number: str) -> Optional[dict]:
    """Call Mono CAC lookup. Returns the raw `data` payload or None."""
    if not _mono_enabled() or not (rc_number or "").strip():
        return None
    return await _mono_post("/v3/lookup/cac", {"rc_number": rc_number.strip()}, settings.MONO_SECRET_KEY)


# ---- Cache helpers -----------------------------------------------------------

def get_cached_nin(profile: Optional[UserProfile], nin: str) -> Optional[dict]:
    """Return cached Mono NIN data when it matches the NIN being verified."""
    if not profile or not profile.nin_verification_data:
        return None
    cached = profile.nin_verification_data
    if isinstance(cached, dict) and cached.get("nin") == (nin or "").strip():
        return cached
    return None


def save_nin_cache(profile: UserProfile, result: dict) -> None:
    profile.nin_verification_data = result


def get_cached_cac(vendor, rc_number: str) -> Optional[dict]:
    cached = getattr(vendor, "cac_verification_data", None)
    if isinstance(cached, dict) and (cached.get("rc_number") or cached.get("nin")) == (rc_number or "").strip():
        return cached
    return None


def save_cac_cache(vendor, rc_number: str, result: dict) -> None:
    vendor.cac_verification_data = {**result, "rc_number": rc_number.strip()}


# ---- Match rules -------------------------------------------------------------

def _norm(s: Optional[str]) -> str:
    return " ".join((s or "").split()).lower()


def _parse_mono_birthdate(value: str) -> Optional[date]:
    """Mono birthdate format: DD-MM-YYYY."""
    try:
        return datetime.strptime((value or "").strip(), "%d-%m-%Y").date()
    except Exception:
        return None


def nin_matches(nin_result: dict, profile: Optional[UserProfile]) -> bool:
    """NIN verification rule: first name, last name and date of birth must match.

    "Other name" (middle name) is a soft check: a mismatch fails, but the check
    is skipped when either side has no middle name.
    """
    if not nin_result or not profile:
        return False
    mono_first = _norm(nin_result.get("firstname"))
    mono_last = _norm(nin_result.get("surname"))
    prof_first = _norm(profile.first_name)
    prof_last = _norm(profile.last_name)
    if not prof_first or not prof_last:
        return False
    if mono_first != prof_first or mono_last != prof_last:
        return False
    # Soft middle-name check: only fails when both sides provide a value.
    mono_middle = _norm(nin_result.get("middlename"))
    prof_middle = _norm(profile.other_name)
    if mono_middle and prof_middle and mono_middle != prof_middle:
        return False
    mono_dob = _parse_mono_birthdate(nin_result.get("birthdate") or "")
    return bool(mono_dob and profile.date_of_birth and mono_dob == profile.date_of_birth)


def cac_matches(cac_result: dict, vendor) -> bool:
    """CAC verification rule: business name must match and status must be active."""
    if not cac_result or not vendor:
        return False
    mono_name = _norm(cac_result.get("business_name") or cac_result.get("company_name"))
    vendor_name = _norm(vendor.business_name)
    if not mono_name or not vendor_name:
        return False
    if mono_name != vendor_name:
        return False
    return _norm(cac_result.get("status") or cac_result.get("company_status")) == "active"
