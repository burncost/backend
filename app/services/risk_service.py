"""
Central vendor risk scoring.

Single source of truth for how a vendor's risk score is computed. Used by:
  - /admin/vendors  (risk_score field, risk_only filter)
  - /admin/vendors/{id}/detail
  - /admin/stats    (high_risk_vendors count)

Scoring model (points add/subtract from a neutral baseline):
  base          = 50 (neutral)
  verification: verified -30 | pending +10 | suspended/rejected/deactivated +30
  tier:         trusted -15 | documented -5 | cac_only 0
  trust:        rating >= 4.5 -15 | rating 3.5-4.4 -5 | rating < 3.0 +20
  reviews:      no reviews +10
  activity:     high total_sales (>= threshold) -10

Clamped to [0, 100]. Buckets: <= 35 Low | 36-60 Medium | > 60 High.
"""
from typing import Optional

# Neutral baseline
BASE_RISK = 50.0

# High-sales volume threshold below which we consider a vendor "low activity"
# and the risk-reduction threshold at/above which a vendor is "established".
HIGH_SALES_THRESHOLD = 10_000_000.0


def _status(vendor) -> str:
    return (
        vendor.verification_status.value
        if hasattr(vendor.verification_status, "value")
        else str(vendor.verification_status)
    )


def compute_risk_score(
    verification_status: str,
    verification_tier: Optional[str] = None,
    rating: Optional[float] = None,
    total_reviews: Optional[int] = None,
    total_sales: Optional[float] = None,
) -> int:
    """Compute a vendor's risk score from milestone signals. Clamped to [0, 100]."""
    score = BASE_RISK

    # Verification milestone
    status = (verification_status or "").lower()
    if status == "verified":
        score -= 30
    elif status in ("suspended", "rejected", "deactivated"):
        score += 30
    else:  # pending / unknown
        score += 10

    # Tier milestone
    tier = (verification_tier or "").lower()
    if tier == "trusted":
        score -= 15
    elif tier == "documented":
        score -= 5
    # cac_only / unknown -> no change

    # Rating / review trust signals
    r = float(rating or 0)
    if r >= 4.5:
        score -= 15
    elif r >= 3.5:
        score -= 5
    elif 0 < r < 3.0:
        score += 20

    # No reviews is a mild risk signal
    if not total_reviews:
        score += 10

    # Established activity reduces risk
    if float(total_sales or 0) >= HIGH_SALES_THRESHOLD:
        score -= 10

    return max(0, min(100, int(round(score))))


def risk_from_vendor(vendor) -> int:
    """Compute the risk score from an ORM Vendor instance."""
    return compute_risk_score(
        verification_status=_status(vendor),
        verification_tier=vendor.verification_tier,
        rating=float(vendor.rating) if vendor.rating else 0.0,
        total_reviews=vendor.total_reviews,
        total_sales=float(vendor.total_sales) if vendor.total_sales else 0.0,
    )


def is_high_risk(score: int) -> bool:
    """A vendor is "High Risk" when its score lands in the High bucket."""
    return score > 60


def risk_bucket(score: int) -> str:
    if score <= 35:
        return "low"
    if score <= 60:
        return "medium"
    return "high"