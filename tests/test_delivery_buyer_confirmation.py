"""Buyer "confirm receipt" fallback for a POD photo that has no GPS EXIF.

A photo proof must still pass the freshness gate (now 60 minutes) before it can
be saved. When it carries a usable capture time but no GPS, the proof is no
longer dead-ended in the vendor/admin review queue: it is scored `awaiting_buyer`
and the builder confirms receipt. These cover the pure scoring/gating helpers
only - no DB, no HTTP.
"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import app.api.v1.endpoints.deliveries as deliveries
from app.api.v1.endpoints.deliveries import (
    _awaiting_buyer_receipt,
    _buyer_confirm_hint,
    _can_buyer_confirm,
    _pod_today,
    _verify_pod_photo,
)

# Abuja drop-off: a photo geotagged here is inside the 300 m geofence.
DROPOFF_LAT, DROPOFF_LNG = 9.0579, 7.4951


def _job():
    return SimpleNamespace(id="job-1", dropoff_lat=DROPOFF_LAT, dropoff_lng=DROPOFF_LNG)


def _verify(monkeypatch, *, taken_at, lat=None, lng=None):
    """Run the scorer with a stubbed EXIF block (no PIL, no upload).

    The job carries a drop-off so the scorer never needs the DB heartbeat
    lookup; `db` is therefore never touched.
    """
    monkeypatch.setattr(
        deliveries,
        "extract_photo_metadata",
        lambda content: {
            "exif_taken_at": taken_at,
            "exif_lat": lat,
            "exif_lng": lng,
            "raw_exif": {},
        },
    )
    return asyncio.run(_verify_pod_photo(None, _job(), content=b"fake-image"))


def _proof(*, required=True, confirmed_at=None, taken_at=None):
    return SimpleNamespace(
        buyer_confirmation_required=required,
        buyer_confirmed_at=confirmed_at,
        exif_taken_at=taken_at,
    )


def _today_at_10am():
    return datetime.combine(_pod_today(), datetime.min.time()) + timedelta(hours=10)


# -- _verify_pod_photo: no-GPS reroute ---------------------------------

def test_no_gps_with_timestamp_awaits_buyer(monkeypatch):
    out = _verify(monkeypatch, taken_at=datetime.utcnow())
    assert out["verification_status"] == "awaiting_buyer"
    assert out["buyer_confirmation_required"] is True
    # The buyer resolves this, so it must stay out of the vendor/admin queue.
    assert out["needs_review"] is False
    assert out["blocked_reason"] is None


def test_no_gps_without_timestamp_is_still_blocked(monkeypatch):
    out = _verify(monkeypatch, taken_at=None)
    assert out["blocked_reason"]
    assert out["buyer_confirmation_required"] is False


def test_gps_photo_never_requires_buyer_confirmation(monkeypatch):
    out = _verify(monkeypatch, taken_at=datetime.utcnow(), lat=DROPOFF_LAT, lng=DROPOFF_LNG)
    assert out["verification_status"] == "verified"
    assert out["buyer_confirmation_required"] is False


# -- freshness window is 60 minutes ------------------------------------

def test_freshness_window_allows_59_minutes(monkeypatch):
    out = _verify(monkeypatch, taken_at=datetime.utcnow() - timedelta(minutes=59))
    assert out["blocked_reason"] is None
    assert out["is_live"] is True


def test_freshness_window_blocks_61_minutes(monkeypatch):
    out = _verify(monkeypatch, taken_at=datetime.utcnow() - timedelta(minutes=61))
    assert out["blocked_reason"]
    assert out["is_live"] is False
    assert "60 min" in out["blocked_reason"]


# -- _can_buyer_confirm -------------------------------------------------

def test_can_confirm_only_on_the_capture_day():
    assert _can_buyer_confirm(_proof(taken_at=_today_at_10am())) is True
    yesterday = _today_at_10am() - timedelta(days=1)
    assert _can_buyer_confirm(_proof(taken_at=yesterday)) is False


def test_can_confirm_requires_the_flag_and_no_prior_confirmation():
    today = _today_at_10am()
    assert _can_buyer_confirm(_proof(required=False, taken_at=today)) is False
    assert _can_buyer_confirm(_proof(taken_at=today, confirmed_at=datetime.utcnow())) is False
    assert _can_buyer_confirm(_proof(taken_at=None)) is False
    assert _can_buyer_confirm(None) is False


# -- _buyer_confirm_hint ------------------------------------------------

def test_confirm_hint_explains_each_state():
    today = _today_at_10am()
    yesterday = today - timedelta(days=1)
    assert "earlier day" in (_buyer_confirm_hint(_proof(taken_at=yesterday)) or "")
    assert "no capture time" in (_buyer_confirm_hint(_proof(taken_at=None)) or "")
    assert _buyer_confirm_hint(_proof(taken_at=today))
    # Nothing to say once the buyer has confirmed (or when it was never needed).
    assert _buyer_confirm_hint(_proof(taken_at=today, confirmed_at=datetime.utcnow())) is None
    assert _buyer_confirm_hint(_proof(required=False, taken_at=today)) is None


# -- _awaiting_buyer_receipt: the driver's "mark delivered" gate ------------
#
# The driver must NOT be able to close a no-location job himself - the buyer (or
# an admin accepting on their behalf) finalises it. The gate is what stops a
# receipt that was never confirmed from being reported as "delivered".

def test_no_gps_unconfirmed_proof_blocks_the_driver():
    today = _today_at_10am()
    assert _awaiting_buyer_receipt(_proof(taken_at=today)) is True
    # The capture day does not matter here: the job stays blocked until the
    # receipt is confirmed, however late that confirmation arrives.
    assert _awaiting_buyer_receipt(_proof(taken_at=today - timedelta(days=3))) is True


def test_confirmed_or_geotagged_proof_unlocks_the_driver():
    today = _today_at_10am()
    assert _awaiting_buyer_receipt(_proof(taken_at=today, confirmed_at=datetime.utcnow())) is False
    assert _awaiting_buyer_receipt(_proof(required=False, taken_at=today)) is False
    assert _awaiting_buyer_receipt(None) is False
