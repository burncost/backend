"""Expiry is reflected in the promo payload, not only at redemption.

Regression pin: an expired code kept rendering as "Active" on the supplier
dashboard and the admin Promotions page because both UIs only looked at
`is_active`. The row must stay listed (its owner can still extend or delete it),
so the fix is a derived `is_expired` flag on `PromoCodeResponse` that every
listing endpoint serialises - using the exact boundary checkout enforces via
`promo_service.failure_reason`.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.promo import PromoCodeResponse
from app.services.promo_service import REASON_EXPIRED, failure_reason


def _orm_like(**overrides):
    """A stand-in for a `PromoCode` row: UUID ids, Decimal money columns."""
    base = dict(
        id=uuid4(),
        code="SAVE1000",
        description=None,
        discount_amount=Decimal("1000.00"),
        max_uses=0,
        current_uses=0,
        max_uses_per_user=0,
        min_order_amount=Decimal("0.00"),
        is_active=True,
        vendor_id=uuid4(),
        scope_type="vendor",
        scope_id=None,
        expires_at=None,
        created_at=datetime.utcnow(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_past_expiry_is_flagged_expired():
    row = _orm_like(expires_at=datetime.utcnow() - timedelta(seconds=1))
    payload = PromoCodeResponse.model_validate(row).model_dump(mode="json")

    assert payload["is_expired"] is True
    # The stored flag is untouched: the row stays listed for editing/deletion.
    assert payload["is_active"] is True


def test_future_expiry_is_not_expired():
    row = _orm_like(expires_at=datetime.utcnow() + timedelta(days=1))
    assert PromoCodeResponse.model_validate(row).is_expired is False


def test_missing_expiry_never_expires():
    assert PromoCodeResponse.model_validate(_orm_like(expires_at=None)).is_expired is False


def test_flag_agrees_with_checkout_rules():
    """Badge and checkout must never disagree about the same row."""
    past = _orm_like(expires_at=datetime.utcnow() - timedelta(minutes=1))
    future = _orm_like(expires_at=datetime.utcnow() + timedelta(minutes=1))
    never = _orm_like(expires_at=None)

    for row in (past, future, never):
        flagged = PromoCodeResponse.model_validate(row).is_expired
        reason = failure_reason(row, base=5000.0)
        assert flagged is (reason == REASON_EXPIRED)
