"""BOQ price sourcing: database first, online average price only as a fallback.

Covers the sourcing contract for the BOQ pipeline:
  1. our own database is the only source of a verified rate, and it always wins;
  2. when the DB has no price, the observed online average price reference
     completes the total;
  3. only when neither has a figure does the flagged AI estimate apply;
  4. the reference never says where its figures came from (user-facing wording is
     "Online average price"), and it is unit-safe, so a per-bag rate can never be
     applied to a line measured in m2.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.online_average_prices import (
    PROMOTED_LABEL,
    PROMOTED_SOURCE,
    find_online_average,
)
from app.services.price_service import CITY_FACTORS, DBProduct, PriceService


def _run(coro):
    return asyncio.run(coro)


def _service_with_db(prices: dict) -> PriceService:
    service = PriceService()
    service._load_prices = AsyncMock(return_value=prices)
    return service


@pytest.fixture(autouse=True)
def _no_live_price_search(monkeypatch):
    """Keep this module offline.

    Tier 3 of ``get_rate`` is a real web search (billed, network-bound, disk
    cached for a week), so with a working connection it can price an item before
    the offline reference tier these tests assert on. Every case here is meant to
    be deterministic, so the live search is pinned to "no result".
    """
    monkeypatch.setattr(
        PriceService, "get_online_market_rate", AsyncMock(return_value=None)
    )


class FakeEngine:
    """PriceEngine stand-in whose AI estimate is off unless explicitly used."""

    def __init__(self, estimate=None):
        self.estimate = estimate
        self.estimate_calls = 0

    async def get_market_rate_estimate(self, description, city="Abuja"):
        self.estimate_calls += 1
        return self.estimate

    def get_online_average_rate(self, description, city="Abuja", unit=None):
        return find_online_average(
            description, city, unit=unit, city_factor=CITY_FACTORS.get(city, 1.0)
        )


def _db_cement() -> DBProduct:
    return DBProduct(
        product_code="MAT-001",
        name="Cement - Portland Type I (50kg)",
        category="cement",
        unit="bag",
        unit_price=12_500.0,
        city="Abuja",
        last_updated="2026-09-01",
    )


# ── 1. The database always wins ──────────────────────────────────────────────

def test_database_price_beats_online_average():
    """A DB rate for the same item must never be replaced by an online average."""
    service = _service_with_db({"MAT-001": _db_cement()})
    engine = FakeEngine()
    service.engine = engine

    item = {
        "item_code": "SUB-001",
        "description": "Cement - Portland Type I (50kg)",
        "quantity": 100.0,
        "unit": "bag",
    }
    enriched, _discrepancy, _oos = _run(service._match_item(item, "Abuja"))

    assert enriched["rate_source"] == "database"
    assert enriched.get("price_source") in (None, "database")
    assert enriched["db_price_matched"] is True
    assert enriched["adjusted_rate"] == 12_500.0
    assert enriched["amount"] == 1_250_000.0
    assert engine.estimate_calls == 0


# ── 2. No DB price -> online average price completes the total ───────────────

def test_item_without_db_price_is_priced_from_online_average():
    service = _service_with_db({})
    engine = FakeEngine()
    service.engine = engine

    item = {
        "item_code": "SUB-002",
        "description": "Cement - Portland 42.5R (50kg)",
        "quantity": 20.0,
        "unit": "bag",
    }
    enriched, _discrepancy, out_of_stock = _run(service._match_item(item, "Abuja"))

    assert enriched["price_source"] == PROMOTED_SOURCE
    assert enriched["rate_source"] == PROMOTED_SOURCE
    assert enriched["price_basis"] == PROMOTED_LABEL
    assert enriched.get("verified") is None  # no negative flag is shipped
    assert enriched["db_price_matched"] is False
    assert enriched["out_of_stock"] is False
    assert out_of_stock is None
    # Lagos median 14,000 divided by the Lagos factor (1.05) = Abuja baseline.
    assert enriched["adjusted_rate"] == 13_333.33
    assert enriched["amount"] == round(20.0 * 13_333.33, 2)
    assert engine.estimate_calls == 0, "the AI estimate must not be reached"


def test_boq_total_is_completed_from_online_average_when_db_is_empty():
    """Element and contract-sum arithmetic still complete on a DB-less bill."""
    service = _service_with_db({})
    service.engine = FakeEngine()

    elements = [{
        "elementName": "Substructure",
        "items": [
            {"item_code": "SUB-001", "description": "Sharp sand filling", "quantity": 30.0, "unit": "m3"},
            {"item_code": "SUB-002", "description": "Sandcrete block 225mm", "quantity": 500.0, "unit": "nr"},
        ],
    }]
    enriched, _discrepancies, _oos = _run(service.enrich_boq_elements(elements, "Lagos"))
    totals = service.recalculate_totals(enriched)

    sand_rate = round(70_000.0 * 0.32, 2)   # Lagos: no city adjustment
    block_rate = 1_000.0
    expected_sub_total = round(30.0 * sand_rate + 500.0 * block_rate, 2)

    for element in enriched:
        for item in element["items"]:
            assert item["rate_source"] == PROMOTED_SOURCE
            assert item["amount"] > 0

    assert totals["sub_total"] == expected_sub_total
    assert totals["total_contract_sum"] > totals["sub_total"]


def test_get_rate_uses_online_average_after_db_miss():
    service = _service_with_db({})
    service.engine = FakeEngine()

    result = _run(service.get_rate("Sharp sand filling", "Lagos", unit="m3"))

    assert result is not None
    assert result["price_source"] == PROMOTED_SOURCE
    assert result.get("verified") is None  # no negative flag is shipped
    assert result["rate"] == round(70_000.0 * 0.32, 2)


# ── 3. AI estimate only when neither the DB nor the reference can price it ────

def test_ai_estimate_is_last_resort_only():
    service = _service_with_db({})
    engine = FakeEngine(estimate={
        "rate": 5_000.0,
        "unit": "m2",
        "product_name": "Widgetron 9mm",
        "product_code": "",
        "price_source": "ai_estimate",
        "source": "ai_estimate",
        "confidence": 0.3,
        "city": "Abuja",
    })
    service.engine = engine

    result = _run(service.get_rate("Widgetron 9mm panel", "Abuja", unit="m2"))

    assert result["price_source"] == "ai_estimate"
    assert engine.estimate_calls == 1


def test_dbless_item_unknown_to_reference_is_still_flagged_unpriced():
    service = _service_with_db({})
    engine = FakeEngine(estimate=None)
    service.engine = engine

    item = {
        "item_code": "PRE-001",
        "description": "Site clearance, including removal of shrubs",
        "quantity": 12.96,
        "unit": "m2",
        "rate": 0,
    }
    enriched, _discrepancy, out_of_stock = _run(service._match_item(item, "Abuja"))

    assert enriched["rate_source"] == "unavailable"
    assert enriched["price_status"] == "unavailable"
    assert enriched["out_of_stock"] is True
    assert out_of_stock is not None


# ── 4. Unit safety and city adjustment ───────────────────────────────────────

def test_unit_mismatch_blocks_online_average():
    """A per-bag rate must never be applied to a line measured in m2."""
    assert find_online_average("Cement - Portland 42.5R (50kg)", "Abuja", unit="m2") is None
    assert find_online_average("Cement - Portland 42.5R (50kg)", "Abuja", unit="bag") is not None


def test_unitless_lookup_needs_the_selling_unit_in_the_description():
    assert find_online_average("Dangote cement", "Abuja") is None
    assert find_online_average("Dangote cement per bag", "Abuja") is not None


def test_online_average_is_adjusted_per_city():
    abuja = find_online_average("Cement 50kg", "Abuja", unit="bag", city_factor=1.0)
    lagos = find_online_average("Cement 50kg", "Lagos", unit="bag", city_factor=1.05)

    assert abuja["rate"] == 13_333.33
    assert lagos["rate"] == 14_000.0


def test_spec_match_beats_bare_category_keyword():
    """'cable 2.5mm' must not be priced from the 1.5mm row."""
    small = find_online_average("Coleman cable 1.5mm", "Lagos", unit="m", city_factor=1.05)
    larger = find_online_average("Coleman cable 2.5mm", "Lagos", unit="m", city_factor=1.05)

    assert larger["rate"] > small["rate"]


# ── 5. Provenance wording ────────────────────────────────────────────────────

def test_reference_never_names_or_links_its_upstream_listing():
    """No payload field, and no source line, may point at the upstream site."""
    module_path = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "online_average_prices.py"
    )
    source = module_path.read_text(encoding="utf-8").lower()

    assert "landalyst" not in source
    assert "http" not in source
    assert "www." not in source

    payload = find_online_average("Sharp sand filling", "Abuja", unit="m3")
    assert payload["price_basis"] == PROMOTED_LABEL
    assert "landalyst" not in repr(payload).lower()
    assert "http" not in repr(payload).lower()


def test_price_truth_service_stays_database_only():
    """The verified-price path must not gain an online-average fallback."""
    from app.services.price_service import PriceTruthService

    service = _service_with_db({})
    service.engine = FakeEngine()

    assert _run(PriceTruthService(service).get_verified_rate("Cement 50kg", "Abuja")) is None
