"""BOQ price sourcing: products (discount first) -> material_rates -> online -> AI.

The contract this file locks down:

  1. A line is priced from OUR OWN database first, and the vendor catalogue
     (`products`) comes before `material_rates`: the discount price wins over
     the list price, and the cheapest sellable active offer wins overall.
  2. Only when both database stores miss does the live online search run
     (published price lists, then general web search), and only then the
     offline observed online average reference.
  3. A flagged AI estimate is the last resort, and every non-database source is
     never presented as verified.
  4. The live tier is unit-safe, cached, budgeted and never fatal — so a BOQ
     always completes with a total even when the network is unavailable.
"""
import asyncio
import inspect
import json
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services import online_price_search as ops
from app.services import price_service as ps
from app.services.price_service import (
    DBProduct,
    PriceService,
    PriceTruthService,
    catalogue_search_term,
    effective_product_price,
)

CITY = "Abuja"


def _run(coro):
    return asyncio.run(coro)


class FakeEngine:
    """PriceEngine double: no network, no model, explicit answers only."""

    def __init__(self, average=None, estimate=None):
        self.average = average
        self.estimate = estimate
        self.average_calls = 0
        self.estimate_calls = 0

    def get_online_average_rate(self, description, city="Abuja", unit=None):
        self.average_calls += 1
        return self.average

    async def get_market_rate_estimate(self, description, city="Abuja"):
        self.estimate_calls += 1
        return self.estimate


def _catalogue_rate(rate: float = 11_500.0, source: str = "product_discount") -> dict:
    return {
        "rate": rate,
        "unit": "bag",
        "product_name": "Dangote Cement 50kg",
        "product_code": "CEM-001",
        "price_source": source,
        "source": "database",
        "verified": True,
        "confidence": 1.0,
        "city": CITY,
        "product_id": "11111111-1111-1111-1111-111111111111",
        "discounted": source == "product_discount",
        "price_basis": "Discounted vendor price",
        "in_stock": 40,
    }


def _online_rate(rate: float = 13_250.0) -> dict:
    return {
        "rate": rate,
        "unit": "bag",
        "product_name": "Cement 50kg bag (online)",
        "product_code": "",
        "price_source": ps.LIVE_SOURCE,
        "source": ps.LIVE_SOURCE,
        "confidence": 0.7,
        "city": CITY,
        "price_basis": ps.LIVE_LABEL,
        "source_name": "prices.example",
        "source_url": "https://prices.example/list",
    }


def _average_rate(rate: float = 13_333.33) -> dict:
    return {
        "rate": rate,
        "unit": "bag",
        "product_name": "Cement 50kg bag (online average)",
        "product_code": "",
        "price_source": "online_average",
        "source": "online_average",
        "confidence": 0.55,
        "city": CITY,
        "price_basis": "Online average price",
    }


def _estimate_rate(rate: float = 14_500.0) -> dict:
    return {
        "rate": rate,
        "unit": "bag",
        "product_name": "Cement 50kg bag (estimate)",
        "product_code": "",
        "price_source": "ai_estimate",
        "source": "ai_estimate",
        "confidence": 0.3,
        "city": CITY,
    }


def _product(**over) -> dict:
    product = {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "Dangote Cement 50kg",
        "sku": "CEM-002",
        "status": "active",
        "base_price": 13_000,
        "discount_price": None,
        "quantity": 25,
        "unit_of_measure": "bag",
        "vendor_id": "33333333-3333-3333-3333-333333333333",
        "updated_at": "2026-09-01T00:00:00",
    }
    product.update(over)
    return product



# ── Tier 1 helpers ──────────────────────────────────────────────────────────

def test_catalogue_search_term_strips_bill_wording():
    assert catalogue_search_term("Provide and fix Dangote cement 50kg (bag)") == "Dangote cement 50kg"
    assert catalogue_search_term("Supply and install PORCELAIN TILE 600x600mm") == "PORCELAIN TILE 600x600mm"


def test_effective_product_price_prefers_the_discount_price():
    rate, source = effective_product_price(_product(base_price=13_000, discount_price=11_500))
    assert rate == 11_500
    assert source == "product_discount"


def test_effective_product_price_falls_back_to_the_list_price():
    rate, source = effective_product_price(_product(base_price=13_000, discount_price=None))
    assert (rate, source) == (13_000, "product_catalogue")
    # A zero discount is not a price.
    rate, source = effective_product_price(_product(base_price=13_000, discount_price=0))
    assert (rate, source) == (13_000, "product_catalogue")


# ── Tier 1: the product catalogue ───────────────────────────────────────────

def test_catalogue_tier_never_opens_a_connection_without_a_session():
    """A session-less service (offline/test use) must not touch Postgres."""
    service = PriceService()
    with patch(
        "app.core.database.AsyncSessionLocal",
        side_effect=AssertionError("no implicit DB connection"),
    ):
        assert _run(service.get_catalogue_rate("Dangote cement 50kg", CITY, "bag")) is None


def test_catalogue_tier_prices_from_the_discount_and_lowest_offer():
    offers = [
        _product(sku="A", base_price=13_500, discount_price=12_000, quantity=5),
        _product(sku="B", base_price=13_000, discount_price=11_500, quantity=2),
        _product(sku="C", base_price=12_800, discount_price=12_600, quantity=0),
    ]
    service = PriceService(pg_db=object())  # query is patched; the session is never used
    with patch(
        "app.services.product_service.ProductService.list_products",
        new=AsyncMock(return_value={"products": offers}),
    ):
        result = _run(service.get_catalogue_rate("Provide and fix Dangote cement 50kg", CITY, "bag"))

    assert result["rate"] == 11_500  # cheapest discounted offer, not the cheapest list price
    assert result["price_source"] == "product_discount"
    assert result["discounted"] is True
    assert result["base_price"] == 13_000
    assert result["discount_price"] == 11_500
    assert result["verified"] is True
    assert result["unit"] == "bag"


def test_catalogue_tier_ignores_inactive_and_unpriced_rows():
    offers = [
        _product(sku="DRAFT", status="draft", base_price=9_000),
        _product(sku="OUT", status="out_of_stock", base_price=9_500),
        _product(sku="GONE", status="discontinued", base_price=9_900),
        _product(sku="FREE", status="active", base_price=0, discount_price=0),
        _product(sku="OK", status="active", base_price=13_000),
    ]
    service = PriceService(pg_db=object())
    with patch(
        "app.services.product_service.ProductService.list_products",
        new=AsyncMock(return_value={"products": offers}),
    ):
        result = _run(service.get_catalogue_rate("Dangote cement 50kg", CITY, "bag"))

    assert result["rate"] == 13_000
    assert result["product_code"] == "OK"


def test_catalogue_tier_survives_a_query_failure():
    service = PriceService(pg_db=object())
    with patch(
        "app.services.product_service.ProductService.list_products",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        assert _run(service.get_catalogue_rate("Dangote cement 50kg", CITY, "bag")) is None


def test_catalogue_lookups_are_memoised_per_run():
    """A repeated bill line must not re-query the catalogue."""
    service = PriceService(pg_db=object())
    query = AsyncMock(return_value={"products": [_product()]})
    with patch("app.services.product_service.ProductService.list_products", new=query):
        first = _run(service.get_catalogue_rate("Dangote cement 50kg", CITY, "bag"))
        second = _run(service.get_catalogue_rate("Provide and fix Dangote cement 50kg", CITY, "bag"))

    assert first["rate"] == 13_000
    assert second["rate"] == 13_000
    assert query.await_count == 1



# ── The strict order ────────────────────────────────────────────────────────

def test_catalogue_beats_material_rates():
    service = PriceService()
    service.engine = FakeEngine(average=_average_rate(), estimate=_estimate_rate())
    with patch.object(service, "get_catalogue_rate", AsyncMock(return_value=_catalogue_rate())), patch.object(
        service, "get_online_market_rate", AsyncMock(return_value=_online_rate())
    ), patch.object(
        service,
        "_search_by_keywords",
        AsyncMock(side_effect=AssertionError("material_rates must not be consulted")),
    ):
        result = _run(service.get_rate("Dangote cement 50kg", CITY, unit="bag"))

    assert result["price_source"] == "product_discount"
    assert result["rate"] == 11_500


def test_material_rates_beat_the_online_sources():
    """Our own material_rates.current_price is still a database price."""
    service = PriceService()
    service.engine = FakeEngine(average=_average_rate(), estimate=_estimate_rate())
    db_product = DBProduct(
        product_code="MAT-001",
        name="Cement - Portland Type I (50kg)",
        category="cement",
        unit="bag",
        unit_price=12_500.0,
        city=CITY,
        price_source="material_rate",
    )
    with patch.object(service, "_load_prices", AsyncMock(return_value={"MAT-001": db_product})), patch.object(
        service, "_search_by_keywords", AsyncMock(return_value=db_product)
    ), patch.object(service, "get_catalogue_rate", AsyncMock(return_value=None)), patch(
        "app.services.price_service.search_online_price",
        new=AsyncMock(side_effect=AssertionError("the DB must be consulted first")),
    ):
        result = _run(service.get_rate("Cement 50kg bag", CITY, unit="bag"))

    assert result["source"] == "database"
    assert result["verified"] is True
    assert result["rate"] == 12_500.0
    assert service.engine.average_calls == 0


def test_online_market_beats_the_offline_average_reference(monkeypatch):
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    service = PriceService()
    engine = FakeEngine(average=_average_rate(), estimate=_estimate_rate())
    service.engine = engine
    with patch.object(service, "_load_prices", AsyncMock(return_value={})), patch.object(
        service, "_search_by_keywords", AsyncMock(return_value=None)
    ), patch.object(service, "get_catalogue_rate", AsyncMock(return_value=None)), patch(
        "app.services.price_service.search_online_price", new=AsyncMock(return_value=_online_rate())
    ):
        result = _run(service.get_rate("Cement 50kg bag", CITY, unit="bag"))

    assert result["price_source"] == "online_market"
    assert result.get("verified") is None  # no negative flag is shipped
    assert result["rate"] == 13_250.0
    # The offline reference is only a safety net once the live search is empty.
    assert engine.average_calls == 0
    assert engine.estimate_calls == 0



def test_offline_average_is_used_when_the_online_search_finds_nothing(monkeypatch):
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    service = PriceService()
    engine = FakeEngine(average=_average_rate(), estimate=_estimate_rate())
    service.engine = engine
    with patch.object(service, "_load_prices", AsyncMock(return_value={})), patch.object(
        service, "_search_by_keywords", AsyncMock(return_value=None)
    ), patch.object(service, "get_catalogue_rate", AsyncMock(return_value=None)), patch(
        "app.services.price_service.search_online_price", new=AsyncMock(return_value=None)
    ):
        result = _run(service.get_rate("Cement 50kg bag", CITY, unit="bag"))

    assert result["price_source"] == "online_average"
    assert engine.average_calls == 1
    assert engine.estimate_calls == 0


def test_ai_estimate_is_the_last_resort_and_never_verified(monkeypatch):
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    service = PriceService()
    engine = FakeEngine(average=None, estimate=_estimate_rate())
    service.engine = engine
    with patch.object(service, "_load_prices", AsyncMock(return_value={})), patch.object(
        service, "_search_by_keywords", AsyncMock(return_value=None)
    ), patch.object(service, "get_catalogue_rate", AsyncMock(return_value=None)), patch(
        "app.services.price_service.search_online_price", new=AsyncMock(return_value=None)
    ):
        result = _run(service.get_rate("Widgetron 9mm panel", CITY, unit="m2"))

    assert result["price_source"] == "ai_estimate"
    assert result.get("verified") is None  # no negative flag is shipped
    assert engine.average_calls == 1
    assert engine.estimate_calls == 1


def test_lower_tiers_never_ship_a_negative_verified_flag(monkeypatch):
    """
    Only our own database tiers assert `verified`. The live online, offline
    online-average and AI-estimate payloads leave the field out entirely, so no
    surface (BOQ, chat card, price passport) can render a "not verified" tag.
    """
    live = ops._payload(
        15_900.0,
        "bag",
        "Cement 50kg",
        CITY,
        0.8,
        source_name="prices.example",
        source_url="https://prices.example/cement",
    )
    assert "verified" not in live

    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    service = PriceService()
    service.engine = FakeEngine(average=_average_rate(), estimate=_estimate_rate())
    with patch.object(service, "_load_prices", AsyncMock(return_value={})), patch.object(
        service, "_search_by_keywords", AsyncMock(return_value=None)
    ), patch.object(service, "get_catalogue_rate", AsyncMock(return_value=None)), patch(
        "app.services.price_service.search_online_price", new=AsyncMock(return_value=None)
    ):
        item = {
            "item_code": "SUB-009",
            "description": "Cement 50kg bag",
            "quantity": 2.0,
            "unit": "bag",
            "adjusted_rate": 0,
        }
        enriched, _discrepancy, _oos = _run(service._match_item(item, CITY))

    assert enriched["rate_source"] == "online_average"
    assert enriched.get("verified") is None


def test_quotation_check_never_flags_inflation_from_a_flagless_rate():
    """
    The quotation check derives verification from the price source when the flag
    is absent, so a live/online-average/AI rate can never be treated as a
    verified comparison and can never raise an inflation flag.
    """
    from app.services.boq_generator import BOQGenerator

    src = inspect.getsource(BOQGenerator.verify_quote_text)
    assert "DB_SOURCES" in src
    assert 'db_rate.get("verified", True)' not in src


def test_a_live_online_price_can_never_become_a_verified_db_rate(monkeypatch):
    """The verified path (quotation checks) reads the database only."""
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    service = PriceService()
    with patch.object(service, "_load_prices", AsyncMock(return_value={})), patch.object(
        service, "_search_by_keywords", AsyncMock(return_value=None)
    ), patch.object(service, "get_catalogue_rate", AsyncMock(return_value=None)), patch(
        "app.services.price_service.search_online_price", new=AsyncMock(return_value=_online_rate())
    ):
        assert _run(PriceTruthService(service).get_verified_rate("Cement 50kg bag", CITY)) is None


def test_a_boq_always_totals_even_when_only_an_estimate_exists():
    service = PriceService()
    service.engine = FakeEngine(average=None, estimate=_estimate_rate(rate=1_000.0))
    item = {"item_code": "FIN-001", "description": "Widgetron 9mm panel", "quantity": 10, "unit": "m2"}
    with patch.object(service, "_load_prices", AsyncMock(return_value={})), patch.object(
        service, "_search_by_keywords", AsyncMock(return_value=None)
    ), patch.object(service, "get_catalogue_rate", AsyncMock(return_value=None)):
        enriched, _, _ = _run(
            service.enrich_boq_elements([{"element_name": "Finishes", "items": [item]}], CITY)
        )

    line = enriched[0]["items"][0]
    assert line["adjusted_rate"] == 1_000.0
    assert line["amount"] == 10_000.0

    totals = service.recalculate_totals(enriched)
    assert totals["sub_total"] == 10_000.0
    assert totals["total_contract_sum"] == 12_900.0
    assert totals["total_contract_sum"] > totals["sub_total"]


# ── Tier 3: the live online search ──────────────────────────────────────────

_SAMPLE_HTML = """
<html><body>
  <h1>Building material price list</h1>
  <div class="row">Cement 50kg bag - &#8358;15,200</div>
  <div class="row">Sharp sand per trip — NGN 72,000</div>
  <div class="row">Call 0803 000 0000 for delivery</div>
</body></html>
"""


def test_parse_price_rows_reads_amounts_units_and_skips_junk():
    rows = ops.parse_price_rows(_SAMPLE_HTML)
    prices = [row["price"] for row in rows]
    assert 15_200.0 in prices
    assert 72_000.0 in prices
    assert all("call" not in row["name"].lower() for row in rows)


def test_parse_price_rows_decodes_entities_in_names_and_prices():
    """HTML entities (&#8358;, &nbsp;, &ndash;) must not hide a price."""
    assert ops.strip_html("<p>a&nbsp;b</p>") == "\na b\n"

    rows = ops.parse_price_rows(
        "<li>Cement&nbsp;50kg&nbsp;bag &ndash; &#8358;15,200</li>"
        "<li>Granite &frac12; inch&nbsp;NGN&nbsp;48,500 per m3</li>"
    )
    prices = {row["price"]: row for row in rows}

    assert 15_200.0 in prices, rows
    assert prices[15_200.0]["name"].endswith("bag")
    assert 48_500.0 in prices, rows
    assert prices[48_500.0]["unit"] == "m3"


def test_match_price_row_requires_the_line_unit():
    rows = [{"name": "Cement 50kg bag", "price": 15_200.0, "unit": "bag"}]
    assert ops.match_price_row(rows, "Cement 50kg bag", "m2") is None
    assert ops.match_price_row(rows, "Cement 50kg bag", "bag")["price"] == 15_200.0

    # A row that states no unit is only trusted when its label states it.
    unitless = [{"name": "Cement 50kg", "price": 15_200.0, "unit": ""}]
    assert ops.match_price_row(unitless, "Cement 50kg", "bag") is None
    labelled = [{"name": "Cement 50kg bag", "price": 15_200.0, "unit": ""}]
    assert ops.match_price_row(labelled, "Cement 50kg bag", "bag")["price"] == 15_200.0


def test_match_price_row_picks_the_cheapest_of_equal_matches():
    rows = [
        {"name": "Cement 50kg bag", "price": 15_200.0, "unit": "bag"},
        {"name": "Cement 50kg bag", "price": 14_100.0, "unit": "bag"},
    ]
    assert ops.match_price_row(rows, "Cement 50kg bag", "bag")["price"] == 14_100.0


def test_live_search_is_off_in_tests_and_never_touches_the_network():
    """The suite must stay offline: conftest disables the live tier."""
    ops.clear_cache()
    with patch.object(
        ops, "fetch_price_list", AsyncMock(side_effect=AssertionError("no network in tests"))
    ), patch.object(
        ops, "_from_web_search", AsyncMock(side_effect=AssertionError("no model calls in tests"))
    ):
        assert _run(ops.search_online_price("Cement 50kg bag", CITY, "bag")) is None


def test_live_search_reads_the_configured_price_list(monkeypatch):
    ops.clear_cache()
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    monkeypatch.setattr(ops, "price_list_sources", lambda: ["https://prices.example/list"])
    monkeypatch.setattr(ops, "fetch_price_list", AsyncMock(return_value=_SAMPLE_HTML))

    result = _run(ops.search_online_price("Cement 50kg bag", CITY, "bag"))

    assert result["price_source"] == "online_market"
    assert result["source"] == "online_market"
    assert result.get("verified") is None  # no negative flag is shipped
    assert result["rate"] == 15_200.0
    assert result["source_name"] == "prices.example"
    assert result["source_url"] == "https://prices.example/list"


def test_live_search_falls_back_to_a_general_web_search(monkeypatch):
    """A price list that yields nothing (or is down) defers to general search."""
    ops.clear_cache()
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    monkeypatch.setattr(ops, "price_list_sources", lambda: ["https://prices.example/list"])
    monkeypatch.setattr(ops, "fetch_price_list", AsyncMock(side_effect=RuntimeError("site down")))
    monkeypatch.setattr(ops, "_from_web_search", AsyncMock(return_value=_online_rate()))

    result = _run(ops.search_online_price("Cement 50kg bag", CITY, "bag"))

    assert result["price_source"] == "online_market"
    assert result["rate"] == 13_250.0


def test_live_search_refuses_a_line_with_no_selling_unit(monkeypatch):
    ops.clear_cache()
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    with patch.object(ops, "fetch_price_list", AsyncMock(side_effect=AssertionError("must not run"))):
        assert _run(ops.search_online_price("Cement 50kg", CITY, None)) is None


def test_live_search_caches_misses_so_a_bill_cannot_refetch(monkeypatch):
    ops.clear_cache()
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    fetch = AsyncMock(side_effect=RuntimeError("site down"))
    search = AsyncMock(side_effect=RuntimeError("model unavailable"))
    monkeypatch.setattr(ops, "fetch_price_list", fetch)
    monkeypatch.setattr(ops, "_from_web_search", search)

    assert _run(ops.search_online_price("Widgetron 9mm panel", CITY, "m2")) is None
    assert _run(ops.search_online_price("Widgetron 9mm panel", CITY, "m2")) is None

    assert fetch.await_count == 1  # the failure is cached, not retried per line
    assert search.await_count == 1


def test_web_search_uses_google_search_and_enforces_the_unit(monkeypatch):
    captured = {}

    class _Response:
        def __init__(self, text):
            self.text = text

    class _Models:
        def __init__(self, text):
            self._text = text

        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return _Response(self._text)

    class _Client:
        def __init__(self, text):
            self.models = _Models(text)

    payload = {
        "rate": 15_900,
        "unit": "bag",
        "product_name": "Cement 50kg",
        "source_name": "prices.example",
        "source_url": "https://prices.example/cement",
        "confidence": 0.8,
    }
    monkeypatch.setattr(
        "app.services.gemini_client.get_gemini_client",
        lambda: _Client(json.dumps(payload)),
    )

    result = _run(ops._from_web_search("Cement 50kg bag", CITY, "bag"))

    assert result["rate"] == 15_900.0
    assert result["price_source"] == "online_market"
    assert result.get("verified") is None  # no negative flag is shipped
    assert result["source_url"] == "https://prices.example/cement"
    # General online search must be grounded in web search, not model memory.
    assert captured["config"]["tools"] == [{"google_search": {}}]

    # A rate quoted in the wrong unit is refused outright.
    monkeypatch.setattr(
        "app.services.gemini_client.get_gemini_client",
        lambda: _Client(json.dumps({"rate": 5_900, "unit": "m2", "confidence": 0.9})),
    )
    assert _run(ops._from_web_search("Cement 50kg bag", CITY, "bag")) is None


def test_online_lookups_are_budgeted_per_run(monkeypatch):
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_MAX_PER_RUN", 2)
    calls = []

    async def fake_search(description, city, unit, mongo_db=None):
        calls.append(description)
        return None

    monkeypatch.setattr(ps, "search_online_price", fake_search)
    service = PriceService()
    for index in range(5):
        assert _run(service.get_online_market_rate(f"item {index}", CITY, "bag")) is None

    assert calls == ["item 0", "item 1"]


def test_live_search_is_skipped_entirely_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ONLINE_PRICE_SEARCH_ENABLED", False)
    service = PriceService()
    with patch(
        "app.services.price_service.search_online_price",
        new=AsyncMock(side_effect=AssertionError("disabled")),
    ):
        assert _run(service.get_online_market_rate("Cement 50kg bag", CITY, "bag")) is None
