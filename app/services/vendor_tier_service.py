"""Vendor verification tier service (Tier 0-3, data-driven).

Rules:
- tier_0: signed up (vendor created) - cap 0 (can list, cannot transact)
- tier_1: bio filled - cap 3,000,000
- tier_2: business + NIN provided - cap 20,000,000
- tier_3: CAC provided - no cap + auto "verified"

Only ever upgrades (monotonic / highest tier achieved). Documents/upload
pipeline is disconnected from progression.
"""
from typing import Optional

from app.models.vendor import Vendor
from app.models.user import UserProfile


# Ordered lowest -> highest so we can compare levels.
TIER_ORDER = ["tier_0", "tier_1", "tier_2", "tier_3"]

# Cap in NGN; tier_3 is effectively unlimited.
TIER_CAPS: dict = {
    "tier_0": 0,
    "tier_1": 3_000_000,
    "tier_2": 20_000_000,
    "tier_3": None,
}

# Next data requirement a vendor needs to move up (for UI prompts).
TIER_NEXT_STEP = {
    "tier_0": "Complete your bio to reach Tier 1.",
    "tier_1": "Add your business details and National ID (NIN) to reach Tier 2.",
    "tier_2": "Add your CAC registration to reach Tier 3 (Burncost Verified).",
    "tier_3": "You are fully verified.",
}


def tier_index(tier: str) -> int:
    if tier in TIER_ORDER:
        return TIER_ORDER.index(tier)
    # Legacy codes.
    return {"cac_only": 1, "documented": 2, "trusted": 3}.get(tier, 0)


def tier_cap(tier: str) -> Optional[float]:
    return TIER_CAPS.get(tier if tier in TIER_ORDER else TIER_ORDER[tier_index(tier)], None)


def _has_bio(profile: Optional[UserProfile]) -> bool:
    if not profile:
        return False
    first = (profile.first_name or "").strip()
    last = (profile.last_name or "").strip()
    return bool(first and last and first != "New" and last != "User")


def compute_tier(vendor: Vendor, profile: Optional[UserProfile]) -> str:
    """Lowest tier the vendor currently qualifies for from present data."""
    if not vendor:
        return "tier_0"
    has_cac = bool((vendor.cac_business_registration_number or "").strip())
    has_nin = bool((vendor.nin or "").strip())
    if _has_bio(profile) and has_nin and has_cac:
        return "tier_3"
    if _has_bio(profile) and has_nin:
        return "tier_2"
    if _has_bio(profile):
        return "tier_1"
    return "tier_0"


def resolve_and_apply(vendor: Vendor, profile: Optional[UserProfile]) -> str:
    """Raise-only tier progression. Returns the stored tier code."""
    computed = compute_tier(vendor, profile)
    current = vendor.verification_tier or "tier_0"
    # Keep the highest tier ever achieved.
    if tier_index(computed) > tier_index(current):
        vendor.verification_tier = computed
    final = vendor.verification_tier or "tier_0"
    # Auto "verified" only once the vendor reaches Tier 3 (monotonic keeps it).
    if tier_index(final) >= 3:
        vendor.verification_status = "verified"
    return final