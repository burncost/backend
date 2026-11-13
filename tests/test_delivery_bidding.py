"""Post-confirmation delivery bidding invariants (pure unit tests).

Locks in the money/eligibility rules so a later refactor cannot:
  * let a bid in on a closed window, an assigned job or a non-bid job,
  * accept a negative/absurd delivery fee or one above the distance ceiling,
  * invoice for a job that is already paid, cancelled or expired,
  * double-charge a group by summing jobs twice.

No database, no network - the services are exercised through their pure helpers.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.delivery_bid import (
    BID_ACCEPTED,
    BID_EXPIRED,
    BID_PENDING,
    BID_REJECTED,
    BID_WITHDRAWN,
)
from app.services import bid_dispatch_service as bids
from app.services import shipping_payment_service as shipping


def _job(**overrides):
    base = dict(
        assignment_type="bid",
        status="available",
        shipping_paid=False,
        delivery_fee=Decimal("0.00"),
        bidding_closes_at=datetime.utcnow() + timedelta(hours=1),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Bidding window ──────────────────────────────────────────────────────────

def test_default_window_is_48_hours():
    assert bids.BID_WINDOW_HOURS == 48
    now = datetime(2026, 11, 13, 9, 0, 0)
    assert bids.bid_window_deadline(now) == now + timedelta(hours=48)


def test_bidding_open_only_for_unbidded_bid_jobs():
    assert bids.is_bidding_open(_job()) is True
    # Direct/offer jobs never take bids.
    assert bids.is_bidding_open(_job(assignment_type="offer")) is False
    # An accepted job is decided.
    assert bids.is_bidding_open(_job(status="accepted")) is False
    # Legacy row with no deadline stays open rather than silently closing.
    assert bids.is_bidding_open(_job(bidding_closes_at=None)) is True


def test_bidding_window_closes_at_the_deadline():
    now = datetime.utcnow()
    assert bids.is_bidding_open(_job(bidding_closes_at=now + timedelta(seconds=1)), now) is True
    assert bids.is_bidding_open(_job(bidding_closes_at=now), now) is False
    assert bids.is_bidding_open(_job(bidding_closes_at=now - timedelta(minutes=1)), now) is False


# ── Fee validation ──────────────────────────────────────────────────────────

def test_delivery_fee_is_quantized_to_kobo():
    assert bids.validate_delivery_fee("1500.005") == Decimal("1500.01")
    assert bids.validate_delivery_fee(2500) == Decimal("2500.00")


def test_delivery_fee_rejects_negative_and_absurd_amounts():
    with pytest.raises(ValueError):
        bids.validate_delivery_fee(-1)
    with pytest.raises(ValueError):
        bids.validate_delivery_fee(bids.MAX_DELIVERY_FEE + 1)


def test_delivery_fee_respects_estimate_ceiling_when_enabled(monkeypatch):
    monkeypatch.setattr(bids, "QUOTE_ESTIMATE_MULTIPLIER", Decimal("3"))
    assert bids.validate_delivery_fee(3000, estimate=1000) == Decimal("3000.00")
    with pytest.raises(ValueError):
        bids.validate_delivery_fee(3000.01, estimate=1000)


def test_estimate_ceiling_can_be_disabled(monkeypatch):
    monkeypatch.setattr(bids, "QUOTE_ESTIMATE_MULTIPLIER", Decimal("0"))
    # 10x the estimate is accepted only because the multiplier check is off.
    assert bids.validate_delivery_fee(10_000, estimate=1000) == Decimal("10000.00")


# ── Invoice amount ──────────────────────────────────────────────────────────

def test_unpaid_total_skips_paid_cancelled_and_expired_jobs():
    jobs = [
        _job(delivery_fee=Decimal("1200.00")),
        _job(delivery_fee=Decimal("800.50"), status="accepted"),
        _job(delivery_fee=Decimal("500.00"), shipping_paid=True),
        _job(delivery_fee=Decimal("999.00"), status="cancelled"),
        _job(delivery_fee=Decimal("999.00"), status="expired"),
    ]
    assert bids.unpaid_delivery_total(jobs) == Decimal("2000.50")


def test_unpaid_total_is_zero_for_nothing_owed():
    assert bids.unpaid_delivery_total([]) == Decimal("0.00")
    assert bids.unpaid_delivery_total([_job(shipping_paid=True)]) == Decimal("0.00")


# ── Shipping invoice ────────────────────────────────────────────────────────

def test_shipping_reference_is_prefixed_and_gateway_safe():
    ref = shipping._reference()
    assert ref.startswith("SHP-")
    assert len(ref) <= 100
    assert ref != shipping._reference()
    assert ref[4:].isalnum() and ref[4:].isupper()


def test_billable_statuses_cover_arranged_jobs_only():
    assert set(shipping._BILLABLE_STATUSES) == {
        "available", "accepted", "picked_up", "in_transit", "delivered"
    }
    assert "cancelled" not in shipping._BILLABLE_STATUSES
    assert "expired" not in shipping._BILLABLE_STATUSES


def test_bid_state_constants_stay_distinct():
    states = {BID_PENDING, BID_ACCEPTED, BID_REJECTED, BID_WITHDRAWN, BID_EXPIRED}
    assert len(states) == 5
    assert BID_PENDING == "pending"
