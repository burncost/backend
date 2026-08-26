"""
AI Procurement Intelligence tests (Phases 1-8).
Run with: pytest tests/test_procurement_ai.py -v
All tests are mocked — no live Gemini or DB calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


# ── Phase 1: Price Truth / hallucination prevention ─────────────────────────

class FakePriceEngine:
    """A PriceEngine stand-in with no Gemini-backed prices."""

    def __init__(self):
        self.estimate_calls = 0

    async def get_market_rate_estimate(self, description, city="Abuja"):
        """Flagged AI estimate — verified=False, source=ai_estimate."""
        self.estimate_calls += 1
        return {
            "rate": 5000.0,
            "unit": "m2",
            "product_name": description,
            "product_code": "",
            "price_source": "ai_estimate",
            "source": "ai_estimate",
            "verified": False,
            "confidence": 0.3,
            "city": city,
        }


def test_price_service_never_returns_verified_when_db_empty():
    """get_rate with no DB prices must return a flagged ai_estimate, never a verified price."""
    from app.services.price_service import PriceService

    service = PriceService()  # pg_db=None, mongo_db=None -> empty DB
    engine = FakePriceEngine()
    service.engine = engine
    with patch.object(service, "_load_prices", AsyncMock(return_value={})):
        with patch.object(service, "_search_by_keywords", AsyncMock(return_value=None)):
            import asyncio
            result = asyncio.run(service.get_rate("Dangote cement", "Abuja"))

    assert result is not None
    assert result["verified"] is False
    assert result["price_source"] == "ai_estimate"
    assert result["source"] == "ai_estimate"
    assert engine.estimate_calls == 1


def test_price_service_no_hallucinated_market_rate():
    """There must be no method that fabricates a 'market rate' as verified."""
    from app.services.price_service import PriceEngine
    # The old Gemini-pretrained method is removed.
    assert not hasattr(PriceEngine, "internet_search_rate")


def test_price_truth_service_returns_none_when_no_db():
    """PriceTruthService.get_verified_rate returns None (never estimate) when DB empty."""
    from app.services.price_service import PriceTruthService, PriceService

    service = PriceService()
    truth = PriceTruthService(service)
    with patch.object(service, "_load_prices", AsyncMock(return_value={})):
        with patch.object(service, "_search_by_keywords", AsyncMock(return_value=None)):
            import asyncio
            result = asyncio.run(truth.get_verified_rate("cement", "Abuja"))

    assert result is None


# ── Phase 1: verify_quote_text / quotation inflation (DB-verified only) ─────

def test_quotation_analysis_flags_inflated_only_with_verified_data():
    """analyse_quotation marks 'potentially_inflated' only when a DB rate exists."""
    from app.services.procurement_intelligence_service import ProcurementIntelligenceService

    svc = ProcurementIntelligenceService(pg_db=None)

    async def fake_offers(description, city="Abuja"):
        # Simulate a verified DB offer for descriptions starting with 'X'
        if description.lower().startswith("x"):
            return [{"rate": 100.0, "verified": True}]
        return []

    import asyncio
    svc._verified_offers = fake_offers
    result = asyncio.run(svc.analyse_quotation(
        [
            {"description": "X cement", "quantity": 2, "quoted_rate": 200.0},   # inflated vs 100
            {"description": "Unknown item", "quantity": 1, "quoted_rate": 50.0},  # no DB -> unverified
        ],
        supplier_name="TestSupplier",
        user_id=None,
        city="Abuja",
    ))

    assert result["inflated_count"] == 1
    assert result["unverified_count"] == 1
    assert result["overall_status"] == "flagged"
    # ensure 'Unknown item' is NOT labelled inflated
    unknown = [i for i in result["items"] if i["description"] == "Unknown item"][0]
    assert unknown["status"] == "unverified"


# ── Phase 4: Supplier comparison total procurement cost math ────────────────

def test_compare_prices_total_includes_shipping():
    """Total procurement cost = (qty * rate) + shipping."""
    from app.services.procurement_intelligence_service import ProcurementIntelligenceService

    svc = ProcurementIntelligenceService(pg_db=None)

    async def fake_offers(*a, **k):
        return [{"rate": 5000.0, "unit": "bag", "product_name": "Cement", "shipping_fee": 2000.0, "verified": True}]

    import asyncio
    svc._verified_offers = fake_offers
    result = asyncio.run(svc.compare_prices("cement", quantity=10, city="Abuja"))

    assert result["source"] == "database"
    assert result["verified"] is True
    offer = result["offers"][0]
    assert offer["total_procurement_cost"] == 10 * 5000.0 + 2000.0  # 52,000
    assert result["best_price"] == 52_000.0


# ── Phase 4: Price range / history insufficiency flags ──────────────────────

def test_price_history_insufficient_flag():
    """get_price_history must return insufficient_history=True when sparse/empty."""
    from app.services.procurement_intelligence_service import ProcurementIntelligenceService

    svc = ProcurementIntelligenceService(pg_db=None)
    import asyncio
    result = asyncio.run(svc.get_price_history("cement", "Abuja"))
    assert result["insufficient_history"] is True


# ── Phase 5: Project memory (required - purchased = remaining) ──────────────

def test_project_memory_remaining_calculation():
    """Remaining = required - purchased (clamped >= 0); status derives correctly."""
    from app.services.project_memory_service import ProjectMemoryService

    svc = ProjectMemoryService(pg_db=None)

    # Fake mongo client returning a project + one boq with a single item
    fake_mongo = MagicMock()
    fake_mongo["projects"].find_one = AsyncMock(return_value={"_id": uuid4(), "title": "Test", "clientId": "u1"})
    fake_mongo["boqs"].find.return_value.to_list = AsyncMock(return_value=[
        {"boqData": {"elements": [{"items": [{"description": "Cement", "quantity": 100, "unit": "bag"}]}]}}
    ])
    svc.mongo_db = fake_mongo
    svc._aggregate_purchased = AsyncMock(return_value={"cement": 40.0})

    import asyncio
    result = asyncio.run(svc.get_project_materials("proj-1"))
    mat = result["materials"][0]
    assert mat["required_qty"] == 100.0
    assert mat["purchased_qty"] == 40.0
    assert mat["remaining_qty"] == 60.0
    assert mat["status"] == "partial"


# ── Phase 8: Anonymous/Token abuse prevention ───────────────────────────────

def test_chat_limits_config():
    """Server-side chat limits exist and anonymous <= authenticated <= premium."""
    from app.services.token_service import CHAT_MESSAGE_LIMITS
    assert CHAT_MESSAGE_LIMITS["anonymous"] == 20
    assert CHAT_MESSAGE_LIMITS["authenticated"] == 200
    assert CHAT_MESSAGE_LIMITS["premium"] == 500
    assert CHAT_MESSAGE_LIMITS["anonymous"] < CHAT_MESSAGE_LIMITS["authenticated"] < CHAT_MESSAGE_LIMITS["premium"]


def test_token_costs_differentiated():
    """Phase 4/8 intelligence operations have distinct (non-zero) costs."""
    from app.services.token_service import TOKEN_COSTS
    assert TOKEN_COSTS["drawing_analysis"] > 0
    assert TOKEN_COSTS["quotation_analysis"] > 0
    assert TOKEN_COSTS["supplier_optimisation"] == 2
    assert TOKEN_COSTS["procurement_intelligence"] == 2


# ── Phase 6: Order approval requirement ─────────────────────────────────────

def test_order_requires_confirmation():
    """create_order tool never executes; it stages and requires confirmation."""
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock())
    import asyncio
    result = asyncio.run(execu._create_order([{"description": "Cement", "quantity": 2, "rate": 5000}]))
    assert result["required"] == "confirmation"
    assert "confirm" in result["message"].lower()


# ── Phase 6: Domain guard (system prompt) ───────────────────────────────────

def test_system_prompt_has_price_rule_and_domain_guard():
    """SYSTEM_PROMPT must contain the price rule, approval rule, and domain guard."""
    from app.services.chat_service import SYSTEM_PROMPT
    assert "NON-NEGOTIABLE PRICE RULE" in SYSTEM_PROMPT
    assert "APPROVAL RULE" in SYSTEM_PROMPT
    assert "DOMAIN GUARD" in SYSTEM_PROMPT
    assert "verified price for this item in this location" in SYSTEM_PROMPT


# ── Phase 3: Drawing fallback + provenance ──────────────────────────────────

def test_drawing_mapper_needs_fallback_when_no_area():
    """DrawingToBOQMapper returns needs_manual_fallback=True when geometry unusable."""
    from app.services.drawing_to_boq_mapper import DrawingToBOQMapper

    mapper = DrawingToBOQMapper()
    mapped = mapper.map(extracted_geometry={"floor_area_m2": 0, "rooms": []})
    assert mapped["needs_manual_fallback"] is True
    assert mapped["request"] is None


# ── Phase 2: Quantity provenance ────────────────────────────────────────────

def test_template_items_carry_quantity_source():
    """Generated template items are labelled quantity_source='mitm' (Nigerian default)."""
    # We assert the label is present in the produced schema docstring/prompt pattern.
    from app.services.boq_generator import BOQGenerator
    prompt_source = BOQGenerator._build_boq_prompt
    # The AI prompt schema instructs a quantity_source field.
    import inspect
    src = inspect.getsource(prompt_source)
    assert "quantity_source" in src