"""Online rate write-back: cache web observations WITHOUT laundering them.

Contract locked down here:
  1. Only online_market / online_average observations are stored; an AI estimate
     is refused.
  2. A stored row is provisional (verified_at NULL) and keeps its provenance.
  3. A curated row is never overwritten by a web observation.
  4. Re-observing one material updates its single row and appends history.
  5. Provisional rates never count as verified in a BOQ verification summary,
     and their provenance payload says so.
  6. Admin-authored blog HTML is sanitised.
"""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

from app.models.material_rate import MaterialRate
from app.services import online_rate_writeback as wb
from app.services.blog_service import sanitize_html
from app.services.material_rate_sync import rate_lookup_key
from app.services.price_service import DBProduct, PriceTruthService
from app.services.boq_generator import _verification_summary


def _run(coro):
    return asyncio.run(coro)


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


class FakeSession:
    """Minimal AsyncSession double: records adds/flushes, never touches a DB."""

    def __init__(self):
        self.added = []
        self.flushed = 0
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    async def execute(self, query):
        return _EmptyResult()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    async def close(self):
        self.closed += 1


def _observe(session, **overrides):
    kwargs = dict(
        product_name="Cement 50kg bag",
        unit="bag",
        city="Abuja",
        price=14500,
        source="online_average",
        confidence=0.55,
        source_url="https://example.com/cement",
        session=session,
    )
    kwargs.update(overrides)
    return _run(wb.persist_online_price(**kwargs))


# ── Key derivation ───────────────────────────────────────────────────────────

def test_lookup_key_is_stable_across_unit_and_name_noise():
    assert rate_lookup_key("Cement 50kg bag", "bag", "FCT") == rate_lookup_key(
        "Cement 50kg bag (online average)", "Bags", "fct"
    )


def test_lookup_key_varies_by_state():
    assert rate_lookup_key("Sharp sand", "m3", "FCT") != rate_lookup_key(
        "Sharp sand", "m3", "Lagos"
    )


# ── Gates ───────────────────────────────────────────────────────────────────

def test_ai_estimate_is_never_persisted():
    session = FakeSession()
    assert _observe(session, source="ai_estimate", confidence=0.9) is None
    assert session.added == []


def test_low_confidence_is_not_persisted():
    session = FakeSession()
    assert _observe(session, confidence=0.1) is None
    assert session.added == []




# ── Insert / update / curated guard ─────────────────────────────────────────

def test_new_observation_is_stored_as_provisional():
    session = FakeSession()
    with patch.object(wb, "find_cached_rate", return_value=None), \
         patch.object(wb, "get_or_create_online_category") as category:
        category.return_value.id = "cat-1"
        result = _observe(session)

    assert result and result["status"] == "created"
    row = next(o for o in session.added if isinstance(o, MaterialRate))
    assert float(row.current_price) == 14500.0
    assert row.is_provisional is True
    assert row.verified_at is None
    assert row.source == "online_average"
    assert row.source_url == "https://example.com/cement"
    assert row.lookup_key
    assert any(type(o).__name__ == "MaterialRateHistory" for o in session.added)


def test_existing_provisional_row_is_updated_not_duplicated():
    session = FakeSession()
    existing = MaterialRate(
        material_name="Cement 50kg bag",
        unit="bag",
        current_price=14000,
        is_provisional=True,
        source="online_market",
        lookup_key="k",
    )
    existing.id = "rate-1"
    with patch.object(wb, "find_cached_rate", return_value=existing), \
         patch.object(wb, "get_or_create_online_category"):
        result = _observe(session, price=15250)

    assert result and result["status"] == "updated"
    assert float(existing.current_price) == 15250.0
    assert float(existing.previous_price) == 14000.0
    assert existing.is_provisional is True
    assert not any(isinstance(o, MaterialRate) for o in session.added)


def test_legacy_curated_row_without_a_lookup_key_is_not_duplicated():
    """Rows created before the provenance migration have no lookup_key."""
    session = FakeSession()
    curated = MaterialRate(
        material_name="Cement 50kg bag",
        unit="Bags",
        current_price=13000,
        is_provisional=False,
        source="manual",
        lookup_key=None,
    )
    curated.id = "rate-legacy"

    class Result:
        def scalars(self):
            return self
        def all(self):
            return [curated]

    async def fake_execute(query):
        return Result()

    session.execute = fake_execute  # type: ignore[attr-defined]
    with patch.object(wb, "find_cached_rate", return_value=None):
        result = _observe(session, price=99999)

    assert result["status"] == "skipped_curated"
    assert float(curated.current_price) == 13000.0


def test_curated_row_is_never_overwritten():
    session = FakeSession()
    curated = MaterialRate(
        material_name="Cement 50kg bag",
        unit="bag",
        current_price=13000,
        is_provisional=False,
        source="manual",
        verified_at=datetime(2026, 1, 1),
        lookup_key="k",
    )
    curated.id = "rate-2"
    with patch.object(wb, "find_cached_rate", return_value=curated):
        result = _observe(session, price=99999)

    assert result["status"] == "skipped_curated"
    assert float(curated.current_price) == 13000.0
    assert curated.source == "manual"


# ── Honesty propagation ─────────────────────────────────────────────────────

def test_provenance_marks_a_cached_rate_unverified():
    product = DBProduct(
        product_code="MAT-1",
        name="Cement 50kg bag",
        category="cat",
        unit="bag",
        unit_price=14500,
        city="FCT",
        is_provisional=True,
        price_source="online_average",
        confidence=0.55,
        source_url="https://example.com/cement",
    )
    provenance = PriceTruthService._provenance(product)
    assert provenance["verified"] is False
    assert provenance["is_provisional"] is True
    assert provenance["confidence"] == 0.55
    assert "Cached online price" in provenance["price_basis"]


def test_verified_database_rate_stays_verified():
    product = DBProduct(
        product_code="MAT-2", name="Cement", category="c", unit="bag",
        unit_price=14000, city="FCT",
    )
    provenance = PriceTruthService._provenance(product)
    assert provenance["verified"] is True
    assert provenance["is_provisional"] is False


def test_match_item_never_labels_a_cached_online_rate_as_database():
    """The general BOQ path must not launder a provisional row either."""
    from app.services.price_service import PriceService

    service = PriceService()
    provisional = DBProduct(
        product_code="MAT-3",
        name="Cement 50kg bag (online average)",
        category="cement",
        unit="bag",
        unit_price=14500,
        city="Abuja",
        price_source="online_average",
        is_provisional=True,
        source_url="https://example.com/cement",
        confidence=0.55,
    )
    service.get_catalogue_rate = AsyncMock(return_value=None)
    service._search_by_keywords = AsyncMock(return_value=provisional)

    item = {
        "item_code": "SUB-1",
        "description": "Cement 50kg bag",
        "quantity": 10,
        "unit": "bag",
    }
    enriched, _discrepancy, _oos = _run(service._match_item(item, "Abuja"))

    assert enriched["db_price_matched"] is False
    assert enriched["rate_source"] == "online_average"
    assert enriched["is_provisional"] is True
    assert "Cached online price" in enriched["price_basis"]


def test_verification_summary_excludes_provisional_from_verified_count():
    analysis = {
        "verified_count": 1,
        "provisional_count": 1,
        "unverified_count": 0,
        "city": "Abuja",
        "verified_items": [
            {
                "description": "Cement", "unit": "bag", "quantity": 10,
                "quoted_rate": 14000, "quoted_amount": 140000,
                "market_rate": 14500, "status": "fair",
                "match_reason": "matched", "verified": True, "is_provisional": False,
            },
            {
                "description": "uPVC pipe", "unit": "m", "quantity": 5,
                "quoted_rate": 3000, "quoted_amount": 15000,
                "market_rate": 2800, "status": "inflated",
                "match_reason": "matched", "verified": False, "is_provisional": True,
                "deviation_pct": 7.1, "price_source": "online_market",
            },
        ],
    }
    summary = _verification_summary(analysis)
    assert summary["verified_count"] == 1
    assert summary["provisional_count"] == 1
    assert "provisional online reference" in summary["summary_note"]


# ── Blog HTML sanitisation ──────────────────────────────────────────────────

def test_sanitizer_keeps_safe_html_and_strips_scripts():
    raw = '<h2>Hello</h2><script>alert(1)</script><p onclick="x">Safe</p>'
    clean = sanitize_html(raw)
    assert "<h2>Hello</h2>" in clean
    assert "alert(1)" not in clean
    assert "onclick" not in clean
    assert "Safe" in clean


def test_sanitizer_blocks_javascript_urls():
    clean = sanitize_html('<a href="javascript:alert(1)">x</a>')
    assert "javascript:" not in clean
    assert ">x</a>" in clean


def test_writeback_can_be_disabled():
    session = FakeSession()
    with patch.object(wb.settings, "ONLINE_PRICE_WRITEBACK_ENABLED", False):
        assert _observe(session) is None
    assert session.added == []
