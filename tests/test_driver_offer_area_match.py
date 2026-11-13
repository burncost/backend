"""Area matching for driver delivery offers (GET /delivery/offers).

The endpoint used to require an exact, case-sensitive city+state match between the
vendor pickup address and the driver default area, which hid every offer whenever the
two cities differed (vendor "Wuse" vs driver "Gwarinpa", both FCT (Abuja)). These
cover the pure helpers only - no DB, no HTTP.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.api.v1.endpoints.deliveries import (
    _area_key,
    _area_tier,
    _offer_in_area,
    _rank_offers,
)

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _job(city, state, minutes=0, job_id="j"):
    return SimpleNamespace(
        id=job_id,
        pickup_city=city,
        pickup_state=state,
        created_at=T0 + timedelta(minutes=minutes),
    )


# -- _area_key ---------------------------------------------------------

def test_area_key_ignores_case_and_padding():
    assert _area_key("Gwarinpa") == _area_key("  gwarinpa ")
    assert _area_key("FCT (Abuja)") == "fctabuja"
    assert _area_key(None) == ""
    assert _area_key("") == ""


def test_area_key_treats_fct_variants_as_one_area():
    for value in ("FCT", "FCT (Abuja)", "Abuja", "federal capital territory"):
        assert _area_key(value) == "fctabuja"


def test_area_key_keeps_other_states_apart():
    assert _area_key("Lagos") != _area_key("Ogun")
    assert _area_key("Cross River") == "crossriver"


# -- tiering -----------------------------------------------------------

def test_tier_prefers_city_then_state_then_elsewhere():
    state, city = _area_key("FCT (Abuja)"), _area_key("Gwarinpa")
    assert _area_tier(_job("Gwarinpa", "FCT (Abuja)"), state, city) == 0
    assert _area_tier(_job("Wuse", "FCT (Abuja)"), state, city) == 1
    assert _area_tier(_job("Ikeja", "Lagos"), state, city) == 2


def test_offer_in_area_accepts_other_cities_in_the_same_state():
    state, city = _area_key("FCT (Abuja)"), _area_key("Gwarinpa")
    assert _offer_in_area(_job("Wuse", "FCT (Abuja)"), state, city) is True
    assert _offer_in_area(_job("Ikeja", "Lagos"), state, city) is False


def test_offer_in_area_ignores_city_when_driver_has_none():
    state, city = _area_key("FCT (Abuja)"), ""
    assert _offer_in_area(_job("Wuse", "FCT (Abuja)"), state, city) is True
    assert _offer_in_area(_job("Ikeja", "Lagos"), state, city) is False


# -- _rank_offers ------------------------------------------------------

def test_rank_offers_orders_city_then_state_and_hides_other_states():
    jobs = [
        _job("Ikeja", "Lagos", minutes=1, job_id="other-state"),
        _job("Wuse", "FCT (Abuja)", minutes=2, job_id="same-state"),
        _job("Gwarinpa", "FCT (Abuja)", minutes=3, job_id="same-city"),
    ]
    ranked = _rank_offers(jobs, "FCT (Abuja)", "Gwarinpa")
    assert [j.id for j in ranked] == ["same-city", "same-state"]


def test_rank_offers_never_hides_everything_when_state_is_empty():
    jobs = [
        _job("Wuse", "FCT (Abuja)", minutes=1, job_id="a"),
        _job("Maitama", "FCT (Abuja)", minutes=2, job_id="b"),
    ]
    assert [j.id for j in _rank_offers(jobs, "Lagos", "Ikeja")] == ["a", "b"]


def test_rank_offers_without_a_driver_area_keeps_age_order():
    jobs = [
        _job("Ikeja", "Lagos", minutes=5, job_id="new"),
        _job("Wuse", "FCT (Abuja)", minutes=1, job_id="old"),
    ]
    assert [j.id for j in _rank_offers(jobs, None, "")] == ["old", "new"]


def test_vendor_city_may_differ_from_driver_city_in_the_same_state():
    """Regression: exact city equality hid every offer in the state."""
    jobs = [_job("Wuse", "FCT (Abuja)", job_id="wuse")]
    assert [j.id for j in _rank_offers(jobs, "FCT (Abuja)", "Gwarinpa")] == ["wuse"]


def test_fct_aliases_match_the_vendor_state():
    jobs = [_job("Wuse", "FCT (Abuja)", job_id="wuse")]
    assert [j.id for j in _rank_offers(jobs, "FCT", "Gwarinpa")] == ["wuse"]
    assert [j.id for j in _rank_offers(jobs, "Abuja", "Gwarinpa")] == ["wuse"]


def test_rank_offers_survives_a_job_without_created_at():
    missing = _job("Wuse", "FCT (Abuja)", job_id="missing")
    missing.created_at = None
    present = _job("Maitama", "FCT (Abuja)", job_id="present")
    ranked = _rank_offers([missing, present], "FCT (Abuja)", "Gwarinpa")
    assert [j.id for j in ranked] == ["missing", "present"]
