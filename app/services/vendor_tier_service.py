"""Vendor verification tier service (3 tiers, data-driven).

Rules:
- starter: signed up alone - cap 3,000,000
- verified_vendor: NIN provided - cap 10,000,000
- enterprise: CAC provided - no cap + auto "verified" + BurnCost badge

Only ever upgrades (monotonic / highest tier achieved). The old document/upload
pipeline is bypassed (not deleted): NIN/CAC numbers are persisted on the vendor
and logged to vendor_documents for manual admin review.
"""
from typing import Optional

from app.models.vendor import Vendor
from app.models.user import UserProfile


# Ordered lowest -> highest so we can compare levels.
TIER_ORDER = ["starter", "verified_vendor", "enterprise"]

# Legacy codes mapped onto the new tiers (bypass, not delete).
TIER_ALIASES: dict = {
    "tier_0": "starter", "tier_1": "starter",
    "tier_2": "verified_vendor", "tier_3": "enterprise",
    "cac_only": "starter", "documented": "verified_vendor", "trusted": "enterprise",
}

# Cap in NGN; enterprise is effectively unlimited.
TIER_CAPS: dict = {
    "starter": 3_000_000,
    "verified_vendor": 10_000_000,
    "enterprise": None,
}

TIER_LABELS: dict = {
    "starter": "Starter",
    "verified_vendor": "Verified Vendor",
    "enterprise": "Enterprise",
}

# Suggestive upsell copy a vendor sees at each tier (for UI prompts).
TIER_NEXT_STEP = {
    "starter": "Add your NIN to become a Verified Vendor with a ₦10,000,000 limit.",
    "verified_vendor": "Become a BurnCost Verified Enterprise — register your CAC for no transaction cap and the Verified badge.",
    "enterprise": "You're fully verified — no transaction cap, BurnCost Verified badge active.",
}


def normalize_tier(tier: Optional[str]) -> str:
    """Map any legacy/unknown tier code onto the current 3-tier scheme."""
    t = (tier or "").strip()
    return TIER_ALIASES.get(t, t if t in TIER_ORDER else "starter")


def tier_index(tier: Optional[str]) -> int:
    code = normalize_tier(tier)
    return TIER_ORDER.index(code)


def tier_cap(tier: Optional[str]) -> Optional[float]:
    return TIER_CAPS[normalize_tier(tier)]


def _has_bio(profile: Optional[UserProfile]) -> bool:
    if not profile:
        return False
    first = (profile.first_name or "").strip()
    last = (profile.last_name or "").strip()
    return bool(first and last and first != "New" and last != "User")


def compute_tier(vendor: Vendor, profile: Optional[UserProfile]) -> str:
    """Lowest tier the vendor currently qualifies for from present data.

    Signup alone → starter; NIN → verified_vendor; CAC → enterprise.
    """
    if not vendor:
        return "starter"
    has_cac = bool((vendor.cac_business_registration_number or "").strip())
    has_nin = bool((vendor.nin or "").strip())
    if has_nin and has_cac:
        return "enterprise"
    if has_nin:
        return "verified_vendor"
    return "starter"


def resolve_and_apply(vendor: Vendor, profile: Optional[UserProfile]) -> str:
    """Raise-only tier progression. Returns the stored tier code (normalized)."""
    computed = compute_tier(vendor, profile)
    current = normalize_tier(vendor.verification_tier or "starter")
    vendor.verification_tier = current
    # Keep the highest tier ever achieved.
    if tier_index(computed) > tier_index(current):
        vendor.verification_tier = computed
    final = normalize_tier(vendor.verification_tier or "starter")
    vendor.verification_tier = final
    # Auto "verified" once the vendor reaches Enterprise (monotonic keeps it).
    if tier_index(final) >= tier_index("enterprise"):
        vendor.verification_status = "verified"
    return final