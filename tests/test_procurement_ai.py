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


# -- Phase 9 regression tests -------------------------------------------------

def test_tool_response_preserves_function_name_in_contents():
    """Tool results must reach Gemini under their real function name.

    Previously every function_response was named "unknown_tool" because the
    message carried the name under tool_call_id. That silently dropped tool
    output, so the model could not see that iron rods exist and fell back to the
    canned "not in catalog" / "notified vendors" answers."""
    import json
    from app.services.ai_service import ChatAIService
    svc = ChatAIService.__new__(ChatAIService)  # bypass Gemini client init
    messages = [
        {"role": "system", "content": "You are a test."},
        {"role": "user", "content": "Find me 12mm iron rods with the best price"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "compare_prices", "type": "function",
            "function": {"name": "compare_prices", "arguments": json.dumps({"description": "12mm iron rods"})},
        }]},
        {"role": "tool", "tool_call_id": "compare_prices",
         "content": json.dumps({"source": "database", "verified": True,
                                "description": "12mm iron rods", "city": "Abuja",
                                "offers": [{"rate": 8000.0, "product_name": "12mm Reinforcement Rod"}]})},
    ]
    contents = svc._messages_to_contents(messages)
    fn = None
    for c in contents:
        if isinstance(c, dict) and c.get("role") == "user":
            for p in c.get("parts", []):
                if isinstance(p, dict) and "function_response" in p:
                    fn = p["function_response"]
    assert fn is not None, "expected a function_response part"
    assert fn["name"] == "compare_prices"
    assert fn["name"] != "unknown_tool"
    assert fn["response"]["offers"][0]["rate"] == 8000.0


def test_system_prompt_is_sent_as_system_instruction_not_user_turn():
    """The system prompt must reach Gemini as system_instruction.

    Previously _messages_to_contents() flattened it into the first `user` turn,
    so Gemini received [user(system prompt), user("Hi")] and answered the last
    example inside the prompt (e.g. roofing advice) instead of greeting the user.
    """
    from app.services.ai_service import ChatAIService
    svc = ChatAIService.__new__(ChatAIService)  # bypass Gemini client init
    messages = [
        {"role": "system", "content": "SYSTEM INSTRUCTIONS GO HERE."},
        {"role": "user", "content": "Hi"},
    ]

    contents = svc._messages_to_contents(messages)

    # Only the real user turn remains in the contents.
    assert [c["role"] for c in contents] == ["user"]
    assert contents[0]["parts"][0]["text"] == "Hi"
    assert all(
        "SYSTEM INSTRUCTIONS GO HERE." not in p.get("text", "")
        for c in contents
        for p in c.get("parts", [])
    )

    # ...and it is exposed as the system instruction instead.
    assert svc._system_instruction(messages) == "SYSTEM INSTRUCTIONS GO HERE."


def test_orphan_tool_response_is_dropped_on_replay():
    """A function_response must follow its function_call.

    handle_message used to append tool results BEFORE the assistant turn that
    carried the tool_calls, so the stored history was [user, tool, assistant].
    Gemini returns EMPTY text for that shape, which looked like the assistant
    "forgetting" the conversation. Orphans must now be dropped on replay.
    """
    import json as _json

    from app.services.ai_service import ChatAIService
    svc = ChatAIService.__new__(ChatAIService)  # bypass Gemini client init
    poisoned = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "What cement for foundation work?"},
        {"role": "tool", "tool_call_id": "search_products",
         "content": _json.dumps({"products": [{"name": "BUA Cement 50kg"}]})},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "search_products", "type": "function",
            "function": {"name": "search_products",
                         "arguments": _json.dumps({"search": "cement"})},
        }]},
        {"role": "assistant", "content": ""},  # blank turn from a failed call
    ]

    contents = svc._messages_to_contents(poisoned)

    assert [c["role"] for c in contents] == ["user", "model"]
    assert "function_call" in contents[1]["parts"][0]
    # No orphan function_response and no blank text parts survive.
    assert all(
        "function_response" not in p and p.get("text", "x")
        for c in contents for p in c["parts"]
    )


def test_correct_tool_order_yields_call_then_response():
    import json as _json

    from app.services.ai_service import ChatAIService
    svc = ChatAIService.__new__(ChatAIService)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "yes go ahead"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "search_products", "type": "function",
            "function": {"name": "search_products",
                         "arguments": _json.dumps({"search": "cement"})},
        }]},
        {"role": "tool", "tool_call_id": "search_products",
         "tool_name": "search_products",
         "content": _json.dumps({"products": [{"name": "BUA Cement 50kg"}]})},
    ]

    contents = svc._messages_to_contents(messages)

    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert "function_call" in contents[1]["parts"][0]
    assert contents[2]["parts"][0]["function_response"]["name"] == "search_products"


def test_plain_yes_does_not_trigger_signup_action():
    """A bare "yes" must not be treated as purchase intent (regression)."""
    from app.services.chat_service import ChatService
    svc = ChatService.__new__(ChatService)
    svc.is_authenticated = False
    assert not svc._build_actions("Cement is essential for foundations.", [], "yes go ahead")

    # Explicit purchase intent still surfaces the signup action.
    actions = svc._build_actions("Sure.", [], "add to cart")
    assert actions and actions[0].type == "signup"


def test_strip_markdown_cleans_chat_reply():
    """Model markdown must not leak into the plain-text chat bubble."""
    from app.services.chat_service import _strip_markdown

    sample = (
        "To give you the best recommendation and price, please specify:\n"
        "* **Type of roofing sheet** (e.g., stone-coated, aluminum, long-span)\n"
        "* **Your preferred location** (e.g., Lagos, Abuja, Port Harcourt)\n"
        "* **Approximate roof area or number of sheets needed**"
    )

    cleaned = _strip_markdown(sample)

    assert "*" not in cleaned
    assert "#" not in cleaned
    assert "`" not in cleaned
    assert "Type of roofing sheet (e.g., stone-coated, aluminum, long-span)" in cleaned
    assert cleaned.count("• ") == 3

    # Plain-text content that only looks like markdown must survive untouched.
    assert _strip_markdown("Cost is 3 * 4 = 12 naira") == "Cost is 3 * 4 = 12 naira"
    assert _strip_markdown("- dash item\n1. numbered") == "- dash item\n1. numbered"
    assert _strip_markdown("## Heading") == "Heading"


def test_all_array_tool_parameters_declare_items():
    """Gemini rejects ARRAY schemas without `items` (400 INVALID_ARGUMENT)."""
    from app.services.chat_service import TOOL_DEFINITIONS

    def walk(schema, path):
        if not isinstance(schema, dict):
            return
        if str(schema.get("type", "")).lower() == "array":
            assert schema.get("items"), f"{path}: ARRAY schema without items"
        for key, child in (schema.get("properties") or {}).items():
            walk(child, f"{path}.{key}")
        walk(schema.get("items"), f"{path}[]")

    for tool in TOOL_DEFINITIONS:
        func = tool["function"]
        walk(func.get("parameters") or {}, func["name"])


def test_normalize_state_covers_major_cities():
    """Jos/Plateau and other states must map to the material_rates.state value."""
    from app.services.price_service import normalize_state
    assert normalize_state("Jos") == "Plateau"
    assert normalize_state("Plateau") == "Plateau"
    assert normalize_state("Ilorin") == "Kwara"
    assert normalize_state("Owerri") == "Imo"
    assert normalize_state("Warri") == "Delta"
    # Existing mappings must not regress.
    assert normalize_state("Abuja") == "FCT"
    assert normalize_state("Port Harcourt") == "Rivers"


def test_compare_prices_falls_back_to_other_location_rates():
    """With no local rate, a verified rate elsewhere is returned and labelled."""
    import asyncio

    from app.services.procurement_intelligence_service import ProcurementIntelligenceService

    svc = ProcurementIntelligenceService(pg_db=None)

    async def no_local(*a, **k):
        return []

    async def elsewhere(*a, **k):
        return [{
            "rate": 11500.0, "unit": "sheet",
            "product_name": "Stone-Coated Roofing Sheet",
            "city": "FCT", "verified": True, "shipping_fee": 0.0,
            "minimum_order_quantity": 1.0,
        }]

    svc._verified_offers = no_local
    svc._verified_offers_other_states = elsewhere

    result = asyncio.run(svc.compare_prices("stone-coated roofing sheet", 1.0, "Jos"))

    assert result["source"] == "database_other_location"
    assert result["verified"] is True
    assert result["other_location"] is True
    assert result["requested_city"] == "Jos"
    assert result["offers"][0]["city"] == "FCT"
    assert result["offers"][0]["rate"] == 11500.0


def test_price_range_labels_other_location():
    import asyncio

    from app.services.procurement_intelligence_service import ProcurementIntelligenceService

    svc = ProcurementIntelligenceService(pg_db=None)

    async def no_local(*a, **k):
        return []

    async def elsewhere(*a, **k):
        return [{"rate": 11500.0, "unit": "sheet", "city": "FCT", "verified": True}]

    svc._verified_offers = no_local
    svc._verified_offers_other_states = elsewhere

    result = asyncio.run(svc.get_price_range("stone-coated roofing sheet", "Jos"))

    assert result["other_location"] is True
    assert result["requested_city"] == "Jos"
    assert result["range"]["min"] == 11500.0


def test_search_tokens_tolerate_plurals_and_hyphens():
    """Natural-language queries must build matchable token conditions."""
    from app.services.product_service import (
        _category_conditions,
        _search_conditions,
        _token_variants,
    )

    assert "sheet" in _token_variants("sheets")
    # "stone-coated roofing sheets" -> matches "Stone-Coated Roofing Sheet".
    assert _search_conditions("stone-coated roofing sheets") is not None
    assert _search_conditions("   ") is None
    # "roofing sheets" must resolve against the "Roofing Systems" category.
    assert _category_conditions("roofing sheets") is not None


# ── Category filters must never bleed across categories ─────────────────────
# Selecting "Roofing Systems" listed PVC pipes and water tanks: the filter OR-ed
# the tokens ["roofing", "systems"], and "systems" is shared by five of the
# twenty parent categories (Roofing, Ceiling, Plumbing, Electrical, Smart
# Building). Plumbing Systems owns the 110mm PVC pipe and the plastic water tanks
# (sql/seed_20_vendors_with_rates.sql, category a0000011-…0011).
#
# The 20 parent categories exactly as seeded, with the fields the filter matches
# against (sql/seed_categories.sql, the parent INSERT block).
_SEEDED_PARENT_CATEGORIES = [
    ("Cement", "Structure", "material"),
    ("Reinforcement Steel", "Structure", "material"),
    ("Fine Aggregates", "Structure", "material"),
    ("Coarse Aggregates", "Structure", "material"),
    ("Masonry Products", "Structure", "material"),
    ("Burnt Bricks", "Structure", "material"),
    ("Ceiling Systems", "Finishes", "material"),
    ("Tiles & Flooring", "Finishes", "material"),
    ("Timber & Engineered Wood", "Finishes", "material"),
    ("Roofing Systems", "Building Envelope", "material"),
    ("Plumbing Systems", "MEP", "material"),
    ("Sanitary Ware", "MEP", "material"),
    ("Electrical Systems", "MEP", "material"),
    ("Paints & Coatings", "Finishes", "material"),
    ("Doors, Windows & Facades", "Building Envelope", "material"),
    ("Glass", "Building Envelope", "material"),
    ("Smart Building Systems", "Building Services", "material"),
    ("Solar & Renewable Energy", "Building Services", "material"),
    ("Tools & Consumables", "Finishes", "material"),
    ("Equipment & Site Services", "External Works", "material"),
]


def _like_token_groups(cond):
    """One list of required tokens per token group, read from the query.

    Mirrors the SQL the builder emits: `_norm_like` compiles to PostgreSQL
    ``~*`` with ``\\y`` word boundaries over a lowercased, hyphen-normalised
    name/division/material_type. The AND/OR combining is asserted separately via
    ``.operator`` (which is the ``sqlalchemy.sql.operators`` singleton, not the
    public ``and_``/``or_`` builder, and a single-token ``and_()`` collapses to
    its only clause).
    """
    from sqlalchemy.sql.operators import and_ as _op_and

    groups = cond.clauses if getattr(cond, "operator", None) is _op_and else [cond]
    out = []
    for group in groups:
        clauses = group.clauses if hasattr(group, "clauses") else [group]
        out.append([
            str(c.right.value).replace("\\y", "").replace("%", "").lower()
            for c in clauses
        ])
    return out


def _group_matches(patterns, name, division, material_type):
    """One token group matches when a field contains any of its tokens as a word."""
    import re

    fields = [
        (f or "").lower().replace("-", " ")
        for f in (name, division, material_type)
    ]
    return any(
        re.search(rf"\b{re.escape(p)}\b", field)
        for p in patterns
        for field in fields
    )


def test_category_filter_is_strict_by_default():
    """The strict filter ANDs its tokens; only the chat retry may OR them."""
    from sqlalchemy.sql.operators import and_ as op_and, or_ as op_or

    from app.services.product_service import _category_conditions

    strict = _category_conditions("Roofing Systems")
    assert strict.operator is op_and
    assert [len(g) for g in _like_token_groups(strict)] == [3, 3], \
        "one name/division/material_type group per token"

    # Relaxed drops the grouping: SQLAlchemy flattens the nested ORs into one
    # 6-clause OR (2 tokens x 3 fields), i.e. "any token matches any field".
    relaxed = _category_conditions("Roofing Systems", relaxed=True)
    assert relaxed.operator is op_or
    assert len(relaxed.clauses) == 6
    assert all(not hasattr(c, "clauses") for c in relaxed.clauses)

    # A single-token name stays a single group (and_ collapses to it).
    assert [len(g) for g in _like_token_groups(_category_conditions("Glass"))] == [3]

    # No usable token -> no condition (the caller falls back to an exact match).
    assert _category_conditions("") is None
    assert _category_conditions("ab") is None


def test_every_category_pill_matches_only_its_own_category():
    """Regression: a pill must never leak another category's products."""
    from app.services.product_service import _category_conditions

    for name, division, material_type in _SEEDED_PARENT_CATEGORIES:
        groups = _like_token_groups(_category_conditions(name))
        matched = [
            c[0] for c in _SEEDED_PARENT_CATEGORIES
            if all(_group_matches(g, *c) for g in groups)
        ]
        assert matched == [name], f"{name} also matched {matched}"

    # The exact pairing from the bug report.
    roofing = _like_token_groups(_category_conditions("Roofing Systems"))
    assert all(_group_matches(g, "Roofing Systems", "Building Envelope", "material")
               for g in roofing)
    for plumbing in (("Plumbing Systems", "MEP", "material"),
                     ("Ceiling Systems", "Finishes", "material"),
                     ("Electrical Systems", "MEP", "material"),
                     ("Smart Building Systems", "Building Services", "material")):
        assert not all(_group_matches(g, *plumbing) for g in roofing), plumbing[0]


def test_relaxed_category_is_the_opt_in_that_still_broadens():
    """Relaxed single-handedly recovers free text — and re-opens the bleed.

    Hence it is used only by the chat tool, only after a strict miss.
    """
    from app.services.product_service import _category_conditions

    # 1. Recovery: "roofing sheets" matches no single category name (the strict
    #    AND finds nothing), so the relaxed OR is what finds Roofing Systems.
    assert not all(
        _group_matches(g, "Roofing Systems", "Building Envelope", "material")
        for g in _like_token_groups(_category_conditions("roofing sheets"))
    )
    relaxed = _like_token_groups(_category_conditions("roofing sheets", relaxed=True))
    assert any(
        _group_matches(g, "Roofing Systems", "Building Envelope", "material")
        for g in relaxed
    )

    # 2. The bleed relaxed re-opens (why it must never reach the marketplace):
    #    OR-ed tokens let the shared word "systems" reach the plumbing category.
    bleed = _like_token_groups(_category_conditions("Roofing Systems", relaxed=True))
    assert any(
        _group_matches(g, "Plumbing Systems", "MEP", "material") for g in bleed
    )


class _CategoryFakeProductService:
    """Empty for a strict category filter; rows once the filter is relaxed."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def list_products(self, filters, page=1, page_size=20, relaxed_category=False):
        self.calls.append(relaxed_category)
        if relaxed_category:
            return {"products": self.rows, "total": len(self.rows), "page": page}
        return {"products": [], "total": 0, "page": page}


_ROOFING_ROW = {
    "id": "roof-1", "name": "Stone-Coated Roofing Sheet", "base_price": 11500.0,
    "unit_of_measure": "sheet", "quantity": 30, "status": "active",
    "vendor_id": "v1", "brand_name": "RoofPro",
}


def _roofing_executor(product_service):
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock())
    execu.product_service = product_service
    execu._vendor_meta = AsyncMock(side_effect=lambda vid: {
        "marketer": "RoofPro Ltd", "market": "Dei-Dei market, Abuja",
    })
    return execu


def test_search_relaxes_the_category_only_after_a_strict_miss():
    """The chat tool may broaden, and it must report that it did."""
    import asyncio

    fake = _CategoryFakeProductService([_ROOFING_ROW])
    execu = _roofing_executor(fake)

    result = asyncio.run(execu._search_products(category="roofing sheets"))

    assert fake.calls == [False, True], "strict first, then exactly one relaxed retry"
    assert result["relaxed_category_from"] == "roofing sheets"
    assert result["products"][0]["name"] == "Stone-Coated Roofing Sheet"


def test_search_never_relaxes_a_category_that_matched():
    class _HitService:
        def __init__(self):
            self.calls = []

        async def list_products(self, filters, page=1, page_size=20, relaxed_category=False):
            self.calls.append(relaxed_category)
            return {"products": [dict(_ROOFING_ROW)], "total": 1, "page": page}

    import asyncio

    fake = _HitService()
    execu = _roofing_executor(fake)

    result = asyncio.run(execu._search_products(category="Roofing Systems"))

    assert fake.calls == [False], "a strict hit must not trigger a relaxed retry"
    assert result["relaxed_category_from"] is None


def test_public_products_endpoint_never_uses_a_relaxed_category():
    """Marketplace HTTP filtering must stay strict — the relaxed OR was the bug."""
    import inspect

    from app.api.v1.endpoints import products as products_endpoint

    src = inspect.getsource(products_endpoint.list_products)
    assert "relaxed" not in src
    assert "only_verified=True" in src


class _FakeRate:
    """Minimal stand-in for a material_rates row."""

    def __init__(self, material_name):
        self.material_name = material_name


def test_rate_matches_is_precise_then_falls_back():
    """A stone-coated query must not match unrelated '…Sheet' materials.

    The old matcher matched on any single word >3 chars, so "sheet" alone made a
    PVC Ceiling Sheet look like a stone-coated roofing price. The precise pass
    requires every distinctive token; the loose pass keeps colloquial queries
    like "12mm iron rods" working.
    """
    from app.services.procurement_intelligence_service import _rate_matches

    rates = [
        _FakeRate("Stone-Coated Roofing Sheet"),
        _FakeRate("Aluminium Roofing Sheet"),
        _FakeRate("PVC Ceiling Sheet"),
    ]

    precise = [r.material_name for r in _rate_matches(rates, "stone coated roofing sheet")]
    assert precise == ["Stone-Coated Roofing Sheet"]

    # Loose fallback: "12mm iron rods" -> "12mm Reinforcement Rod" (no "iron").
    rods = [_FakeRate("12mm Reinforcement Rod"), _FakeRate("Emulsion Paint (4L)")]
    loose = [r.material_name for r in _rate_matches(rods, "12mm iron rods")]
    assert "12mm Reinforcement Rod" in loose

    # "cement" must not drag in unrelated reinforcement rods ("reinfor-CEMENT").
    mixed = [_FakeRate("Dangote Cement 50kg"), _FakeRate("12mm Reinforcement Rod")]
    assert [r.material_name for r in _rate_matches(mixed, "cement")] == ["Dangote Cement 50kg"]


def test_rate_matches_ignores_shared_dimensions():
    """"12mm iron rods" must not return 12mm Plywood (shared size only)."""
    from app.services.procurement_intelligence_service import _rate_matches

    rates = [
        _FakeRate("12mm Reinforcement Rod"),
        _FakeRate("16mm Reinforcement Rod"),
        _FakeRate("12mm Plywood"),
    ]

    assert [r.material_name for r in _rate_matches(rates, "12mm iron rods")] == ["12mm Reinforcement Rod"]
    # A different size must not answer the query either.
    assert [r.material_name for r in _rate_matches(rates, "16mm iron rods")] == ["16mm Reinforcement Rod"]
    # An empty/unsupported description matches nothing (used to match everything).
    assert _rate_matches(rates, "   ") == []


def test_build_cards_price_passport_carries_location_and_unit():
    """Price Passport items must carry the location and unit, not a bare timestamp."""
    from app.services.chat_service import ChatService

    svc = ChatService.__new__(ChatService)  # card builder needs no instance state
    cards = svc._build_cards([{
        "tool": "compare_prices",
        "result": {
            "source": "database", "verified": True,
            "description": "12mm iron rods", "city": "Abuja",
            "best_price": 175000.0,
            "offers": [{
                "product_name": "12mm Reinforcement Rod", "rate": 8500.0, "unit": "length",
                "city": "FCT", "price_source": "database", "verified": True,
                "last_verified_at": "2026-08-15 12:06:42.379593",
            }],
        },
    }])

    passport = [c for c in cards if c.type == "price_passport"][0]
    item = passport.data["items"][0]
    assert item["city"] == "FCT"
    assert item["unit"] == "length"
    assert item["rate"] == 8500.0
    assert item["last_verified_at"] == "2026-08-15 12:06:42.379593"

    comparison = [c for c in cards if c.type == "price_comparison"][0]
    assert comparison.data["offers"][0]["rate"] == 8500.0


def test_system_prompt_cross_location_and_tool_usage_rules():
    from app.services.chat_service import SYSTEM_PROMPT
    assert "CROSS-LOCATION RULE" in SYSTEM_PROMPT
    assert "TOOL USAGE RULE" in SYSTEM_PROMPT


def test_system_prompt_greetings_beat_domain_guard():
    """Greetings must not be answered with the DOMAIN GUARD redirect."""
    from app.services.chat_service import SYSTEM_PROMPT
    assert "GREETING RULES" in SYSTEM_PROMPT
    assert "offers help" in SYSTEM_PROMPT
    assert "DOMAIN GUARD redirect" in SYSTEM_PROMPT
    assert "plain text with no markdown" in SYSTEM_PROMPT


def test_system_instruction_joins_multiple_system_messages():
    from app.services.ai_service import ChatAIService
    svc = ChatAIService.__new__(ChatAIService)
    assert svc._system_instruction([{"role": "user", "content": "Hi"}]) is None
    assert (
        svc._system_instruction([
            {"role": "system", "content": "A"},
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "B"},
        ])
        == "A\n\nB"
    )


class _FakeProductService:
    """Records the search term used and returns canned results."""
    def __init__(self):
        self.searches = []
    async def list_products(self, filters, page=1, page_size=20, relaxed_category=False):
        self.searches.append(filters.search)
        term = (filters.search or "").lower()
        if "reinforcement" in term or "rebar" in term:
            return {"products": [{"id": "p1", "name": "12mm Reinforcement Rod",
                                  "base_price": 8000.0, "category": "rebar",
                                  "brand_name": "SteelCo", "quantity": 5,
                                  "unit_of_measure": "length", "status": "active",
                                  "rating": 4.5}], "total": 1, "page": page}
        return {"products": [], "total": 0, "page": page}


def test_search_products_falls_back_to_iron_rod_synonyms():
    from app.services.chat_service import ToolExecutor
    execu = ToolExecutor(db=MagicMock())
    execu.product_service = _FakeProductService()
    import asyncio
    result = asyncio.run(execu._search_products(search="iron rods"))
    assert result["products"], "synonym retry should surface the rod"
    assert result["products"][0]["name"] == "12mm Reinforcement Rod"
    assert "iron rods" in (execu.product_service.searches[0]).lower()
    assert any("reinforcement" in s.lower() for s in execu.product_service.searches[1:])


def test_normalize_state_abuja_maps_to_fct():
    from app.services.price_service import normalize_state, CITY_TO_STATE
    assert normalize_state("Abuja") == "FCT"
    assert normalize_state("abuja") == "FCT"
    assert normalize_state("Lagos") == "Lagos"
    assert normalize_state("Rivers") == "Rivers"


# ── Cheapest price / cart MOQ / role-aware chat actions ─────────────────────

_PROD_A = "11111111-1111-1111-1111-111111111111"
_PROD_B = "22222222-2222-2222-2222-222222222222"
_USER_ID = "33333333-3333-3333-3333-333333333333"


class _FakePricedProductService:
    """Canned catalogue for cheapest-price and add-to-cart tests."""

    def __init__(self, products=None, by_id=None):
        self.products = products or []
        self.by_id = by_id or {}

    async def list_products(self, filters, page=1, page_size=20, relaxed_category=False):
        return {"products": self.products, "total": len(self.products), "page": page}

    async def get_product_by_id(self, product_id):
        return self.by_id.get(str(product_id))


def test_get_cheapest_price_ranks_by_effective_price_with_marketer_and_market():
    """The discounted (effective) price wins, and each offer names the marketer/market."""
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock())
    execu.product_service = _FakePricedProductService([
        {"id": _PROD_A, "name": "12mm Reinforcement Rod", "base_price": 9000.0,
         "discount_price": None, "unit_of_measure": "length", "quantity": 40,
         "minimum_order_quantity": 10, "shipping_fee": 5000.0, "status": "active",
         "vendor_id": "v1", "brand_name": "SteelCo"},
        {"id": _PROD_B, "name": "12mm Reinforcement Rod (IronPlus)", "base_price": 8500.0,
         "discount_price": 8000.0, "unit_of_measure": "length", "quantity": 25,
         "minimum_order_quantity": 20, "shipping_fee": 0.0, "status": "active",
         "vendor_id": "v2", "brand_name": "IronPlus"},
    ])
    execu._vendor_meta = AsyncMock(side_effect=lambda vid: {
        "v1": {"marketer": "Raintech Constructions", "market": "Dei-Dei market, Abuja"},
        "v2": {"marketer": "IronPlus Ltd", "market": "Karmo market, Abuja"},
    }.get(vid, {"marketer": "", "market": ""}))

    import asyncio
    result = asyncio.run(execu._get_cheapest_price("12mm rod"))

    # 8000 (discounted) < 9000 (base) -> the discounted listing is cheapest.
    assert result["cheapest_product_id"] == _PROD_B
    top = result["offers"][0]
    assert top["rate"] == 8000.0
    assert top["marketer"] == "IronPlus Ltd"
    assert top["market"] == "Karmo market, Abuja"
    assert top["minimum_order_quantity"] == 20
    # MOQ-inclusive total: 20 (min order) x 8000 + 0 shipping.
    assert top["total_procurement_cost"] == 160000.0
    assert all(o["product_id"] for o in result["offers"])




def test_add_to_cart_refuses_below_minimum_order_quantity():
    """A sub-minimum add is refused and the exact minimum is returned."""
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock(), user_id=_USER_ID)
    execu.product_service = _FakePricedProductService(by_id={
        _PROD_A: {"id": _PROD_A, "name": "12mm Reinforcement Rod", "base_price": 8500.0,
                  "unit_of_measure": "length", "minimum_order_quantity": 10},
    })

    import asyncio
    result = asyncio.run(execu._add_to_cart(_PROD_A, 2))

    assert result["success"] is False
    assert result["minimum_order_quantity"] == 10
    assert "minimum order" in result["error"].lower()
    assert "10" in result["error"], "the minimum must be stated in the message"


def test_add_to_cart_anonymous_returns_guest_signal():
    """Anonymous shoppers get a guest signal so the client adds to the device cart."""
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock())  # no user_id -> anonymous
    execu.product_service = _FakePricedProductService(by_id={
        _PROD_A: {"id": _PROD_A, "name": "Dangote Cement 50kg", "base_price": 9000.0,
                  "unit_of_measure": "bag", "minimum_order_quantity": 5},
    })

    import asyncio
    result = asyncio.run(execu._add_to_cart(_PROD_A, 5))

    assert result["success"] is False
    assert result["guest"] is True
    assert result["product_id"] == _PROD_A
    assert result["quantity"] == 5
    assert result["unit"] == "bag"


def test_add_to_cart_authenticated_persists_line_at_real_price():
    """A valid signed-in add writes the cart line with the real unit price."""
    from app.services.chat_service import ToolExecutor

    db = MagicMock()
    db.execute = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute.return_value = res
    db.commit = AsyncMock()

    execu = ToolExecutor(db=db, user_id=_USER_ID)
    execu.product_service = _FakePricedProductService(by_id={
        _PROD_A: {"id": _PROD_A, "name": "Dangote Cement 50kg", "base_price": 9000.0,
                  "discount_price": 8500.0, "unit_of_measure": "bag",
                  "minimum_order_quantity": 5},
    })

    import asyncio
    result = asyncio.run(execu._add_to_cart(_PROD_A, 5))

    assert result["success"] is True
    assert result["quantity"] == 5
    db.add.assert_called_once()
    db.commit.assert_awaited()


def test_build_cards_cheapest_price_carries_product_id_and_provenance():
    from app.services.chat_service import ChatService
    svc = ChatService.__new__(ChatService)

    cards = svc._build_cards([{
        "tool": "get_cheapest_price",
        "result": {
            "search": "cement",
            "cheapest_product_id": _PROD_A,
            "offers": [{
                "product_id": _PROD_A, "product_name": "Dangote Cement 50kg",
                "rate": 9000.0, "unit": "bag", "marketer": "Raintech Constructions",
                "market": "Dei-Dei market, Abuja", "minimum_order_quantity": 20,
                "billable_quantity": 20, "shipping_fee": 5000.0,
                "total_procurement_cost": 185000.0,
            }],
        },
    }])

    card = [c for c in cards if c.type == "cheapest_price"][0]
    offer = card.data["offers"][0]
    assert offer["product_id"] == _PROD_A
    assert offer["marketer"] == "Raintech Constructions"
    assert offer["market"] == "Dei-Dei market, Abuja"
    assert card.data["cheapest_product_id"] == _PROD_A


def test_build_actions_add_to_cart_carries_quantity_and_minimum():
    from app.services.chat_service import ChatService
    svc = ChatService.__new__(ChatService)
    svc.is_authenticated = True

    tool_results = [{"tool": "search_products", "result": {"products": [
        {"id": _PROD_A, "name": "Dangote Cement 50kg", "minimum_order_quantity": 20},
    ]}}]
    actions = svc._build_actions("Want me to add this to your cart?", tool_results, "add to cart")

    add = [a for a in actions if a.type == "add_to_cart"][0]
    assert add.data["product_id"] == _PROD_A
    assert add.data["minimum_order_quantity"] == 20
    assert add.data["quantity"] == 1


def test_build_actions_anonymous_can_add_to_cart_and_view_product():
    """Anonymous shoppers get actionable cart + view buttons, not signup-only."""
    from app.services.chat_service import ChatService
    svc = ChatService.__new__(ChatService)
    svc.is_authenticated = False

    tool_results = [{"tool": "get_cheapest_price", "result": {"offers": [
        {"product_id": _PROD_A, "product_name": "Dangote Cement", "minimum_order_quantity": 20},
    ]}}]
    actions = svc._build_actions("Sure, add it to your cart.", tool_results, "add to cart")

    types = {a.type for a in actions}
    assert "add_to_cart" in types, "anonymous shoppers must be able to add to the device cart"
    assert "view_product" in types
    view = [a for a in actions if a.type == "view_product"][0]
    assert view.data["product_id"] == _PROD_A
    assert view.data["path_hint"] == "product"


def test_system_prompt_names_marketer_market_and_minimum_order():
    from app.services.chat_service import SYSTEM_PROMPT
    assert "MARKETER" in SYSTEM_PROMPT
    assert "MARKET" in SYSTEM_PROMPT
    assert "MINIMUM ORDER RULE" in SYSTEM_PROMPT
    assert "get_cheapest_price" in SYSTEM_PROMPT



def test_build_actions_skips_second_add_when_cart_already_written():
    """A signed-in add already executed by the tool must not offer a second add."""
    from app.services.chat_service import ChatService
    svc = ChatService.__new__(ChatService)
    svc.is_authenticated = True

    tool_results = [{"tool": "add_to_cart", "result": {
        "success": True, "product_id": _PROD_A, "product_name": "Dangote Cement",
        "quantity": 10, "minimum_order_quantity": 10, "unit": "bag",
    }}]
    actions = svc._build_actions("Added 10 bags to your cart.", tool_results, "add to cart")

    assert not any(a.type == "add_to_cart" for a in actions), "no double-add"
    assert any(a.type == "checkout" for a in actions), "cart is offered instead"



# ── Progressive query broadening (brand + spec + material) ──────────────────

def test_query_variants_drops_spec_and_keeps_brand_plus_material():
    """'Dangote OPC cement' must retry as 'Dangote cement' then 'cement'."""
    from app.services.chat_service import ToolExecutor

    variants = ToolExecutor._query_variants("Dangote OPC cement", set())

    assert variants, "a multi-token query must offer broader retries"
    assert variants[0] == "dangote cement", "brand + material is the closest retry"
    assert "cement" in variants, "the material alone surfaces every stocked brand"
    assert "dangote" in variants, "the brand alone is the last resort"
    assert "dangote opc cement" not in variants, "the literal is never re-tried"
    assert "opc" not in variants, "spec noise is dropped"


def test_query_variants_unknown_brand_still_reaches_material():
    """A brand we don't stock ('Lafarge') still resolves to its material."""
    from app.services.chat_service import ToolExecutor

    variants = ToolExecutor._query_variants("Lafarge cement", set())

    # "Lafarge cement" is already the brand+material form, so the literal is not
    # retried — the material-only search is what surfaces the brands we do stock.
    assert variants[0] == "cement", "'or any one that matches' means the material search"
    assert "lafarge" in variants, "the brand alone is still attempted last"


def test_query_variants_uses_live_brand_tokens():
    """Brand tokens discovered from the Brand table are stripped from the material."""
    from app.services.chat_service import ToolExecutor

    with_brand = ToolExecutor._query_variants("mikano heavy water pump", {"mikano"})
    without_brand = ToolExecutor._query_variants("mikano heavy water pump", set())

    assert with_brand[0] == "mikano water pump", "brand kept, spec noise dropped"
    assert "water pump" in with_brand, "brand stripped -> material-only fallback"
    assert "water pump" not in without_brand, "unknown 'mikano' stays part of the material"


def test_query_variants_single_token_is_left_alone():
    from app.services.chat_service import ToolExecutor

    assert ToolExecutor._query_variants("cement") == []
    assert ToolExecutor._query_variants("") == []


class _BroadenFakeProductService:
    """Returns rows only for the bare material search, like the real catalogue."""

    def __init__(self, hit_term: str = "cement"):
        self.hit_term = hit_term
        self.searches = []

    async def list_products(self, filters, page=1, page_size=20, relaxed_category=False):
        term = (filters.search or "").lower()
        self.searches.append(filters.search)
        if term == self.hit_term:
            return {"products": [{
                "id": "p1", "name": "Dangote Cement 50kg", "base_price": 12500.0,
                "unit_of_measure": "bag", "minimum_order_quantity": 10,
                "quantity": 500, "status": "active", "brand_name": "Dangote Cement",
            }, {
                "id": "p2", "name": "BUA Cement 50kg", "base_price": 12200.0,
                "unit_of_measure": "bag", "minimum_order_quantity": 10,
                "quantity": 400, "status": "active", "brand_name": "BUA Cement",
            }], "total": 2, "page": page}
        return {"products": [], "total": 0, "page": page}


def test_search_products_broadens_only_when_literal_is_empty():
    """One hit on 'cement' after 'Dangote OPC cement' fails, reporting the relaxation."""
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock())
    execu.product_service = _BroadenFakeProductService()

    import asyncio
    result = asyncio.run(execu._search_products(search="Dangote OPC cement"))

    assert len(result["products"]) == 2, "both stocked cement brands are surfaced"
    assert result["matched_query"] == "cement"
    assert result["relaxed_from"] == "Dangote OPC cement"
    # Precision preserved: the literal query is always attempted first.
    assert execu.product_service.searches[0] == "Dangote OPC cement"
    assert "dangote cement" in [s.lower() for s in execu.product_service.searches]


def test_search_products_never_broadens_a_working_query():
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock())
    execu.product_service = _BroadenFakeProductService()

    import asyncio
    result = asyncio.run(execu._search_products(search="cement"))

    assert len(result["products"]) == 2
    assert result["relaxed_from"] is None
    assert result["matched_query"] == "cement"
    assert execu.product_service.searches == ["cement"], "no retry when the first pass hits"


def test_get_cheapest_price_broadens_and_reports_relaxed_from():
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock())
    execu.product_service = _BroadenFakeProductService()
    execu._vendor_meta = AsyncMock(return_value={"marketer": "Raintech", "market": "Dei-Dei, Abuja"})

    import asyncio
    result = asyncio.run(execu._get_cheapest_price("Dangote OPC cement"))

    assert result["total"] == 2
    assert result["matched_query"] == "cement"
    assert result["relaxed_from"] == "Dangote OPC cement"
    assert "No exact catalogue match" in result["explanation"]
    # Cheapest (BUA at 12,200) ranks first; both brands are offered.
    assert [o["product_name"] for o in result["offers"]] == [
        "BUA Cement 50kg", "Dangote Cement 50kg",
    ]


def test_rate_matches_already_resolves_brand_queries():
    """The material_rates matcher needs no extra tier for brand queries.

    Documents why `_rate_matches` was left alone: its strict pass keeps the named
    brand, and its existing loose pass (any distinctive token) already resolves an
    unstocked brand like "Lafarge" to the cement rates we do carry.
    """
    from types import SimpleNamespace
    from app.services.procurement_intelligence_service import _rate_matches

    rates = [
        SimpleNamespace(material_name="Dangote Cement 50kg", unit="bag", current_price=12500.0),
        SimpleNamespace(material_name="BUA Cement 50kg", unit="bag", current_price=12200.0),
        SimpleNamespace(material_name="12mm Reinforcement Rod", unit="length", current_price=8500.0),
    ]

    def names(query):
        return {r.material_name for r in _rate_matches(rates, query)}

    # Named brand that IS stocked -> strict pass keeps just that brand.
    assert names("Dangote OPC cement") == {"Dangote Cement 50kg"}
    # Unstocked brand -> loose pass surfaces every cement brand.
    assert names("Lafarge cement") == {"Dangote Cement 50kg", "BUA Cement 50kg"}
    assert names("Sokoto cement 50kg") == {"Dangote Cement 50kg", "BUA Cement 50kg"}
    # Never drags in a different material that merely shares a size.
    assert "12mm Reinforcement Rod" not in names("Lafarge cement")




# ── Staged guest cart writes (regression: "alright add the 20") ─────────────

def test_guest_staged_add_emits_action_without_any_cart_keywords():
    """A staged guest add must produce the button from the tool result alone.

    Regression: the shopper said "alright add the 20" and the reply said
    "I've added 20 lengths ... to your cart" — neither contains "add to cart" /
    "add it" / "buy", so the keyword-gated button was never emitted and nothing
    was ever written to the device cart.
    """
    from app.services.chat_service import ChatService

    svc = ChatService.__new__(ChatService)
    svc.is_authenticated = False

    tool_results = [{"tool": "add_to_cart", "result": {
        "success": False, "guest": True, "staged": True,
        "product_id": _PROD_A, "product_name": "12mm Reinforcement Rod",
        "quantity": 20, "minimum_order_quantity": 20, "unit": "length", "price": 8200.0,
    }}]

    actions = svc._build_actions(
        "I've added 20 lengths of 12mm Reinforcement Rod to your cart.",
        tool_results,
        "alright add the 20",
    )

    add = [a for a in actions if a.type == "add_to_cart"]
    assert len(add) == 1, "the client write must always be offered for a staged add"
    assert add[0].data["staged"] is True
    assert add[0].data["product_id"] == _PROD_A
    assert add[0].data["quantity"] == 20
    assert add[0].data["minimum_order_quantity"] == 20
    assert add[0].data["unit"] == "length"
    assert add[0].data["price"] == 8200.0, "price travels with the action so the client needs no extra fetch"


def test_guest_staged_add_still_offers_checkout_and_view():
    from app.services.chat_service import ChatService

    svc = ChatService.__new__(ChatService)
    svc.is_authenticated = False

    tool_results = [{"tool": "add_to_cart", "result": {
        "success": False, "guest": True, "staged": True,
        "product_id": _PROD_A, "product_name": "12mm Reinforcement Rod",
        "quantity": 20, "minimum_order_quantity": 20, "unit": "length", "price": 8200.0,
    }}]

    types = {a.type for a in svc._build_actions(
        "20 lengths saved to your cart. Proceed to checkout when ready.", tool_results, "add the 20"
    )}

    assert "add_to_cart" in types
    assert "checkout" in types
    assert "view_product" in types


def test_signed_in_add_is_not_staged_and_offers_no_second_add():
    """The server already wrote the cart, so no staged payload and no add button."""
    from app.services.chat_service import ChatService

    svc = ChatService.__new__(ChatService)
    svc.is_authenticated = True

    tool_results = [{"tool": "add_to_cart", "result": {
        "success": True, "product_id": _PROD_A, "product_name": "12mm Reinforcement Rod",
        "quantity": 20, "minimum_order_quantity": 20, "unit": "length", "price": 8200.0,
    }}]

    actions = svc._build_actions("Added 20 lengths to your cart.", tool_results, "add the 20")

    assert not any(a.type == "add_to_cart" for a in actions), "no double-add"
    assert any(a.type == "checkout" for a in actions)


def test_sub_minimum_refusal_stages_nothing():
    """MOQ refusal returns no staged flag, so no client-side write is offered."""
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock(), user_id=_USER_ID)
    execu.product_service = _FakePricedProductService(by_id={
        _PROD_A: {"id": _PROD_A, "name": "12mm Reinforcement Rod", "base_price": 8500.0,
                  "unit_of_measure": "length", "minimum_order_quantity": 20},
    })

    import asyncio
    result = asyncio.run(execu._add_to_cart(_PROD_A, 10))

    assert result["success"] is False
    assert result.get("staged") is None
    assert result["minimum_order_quantity"] == 20


def test_guest_add_payload_is_staged_with_real_price():
    from app.services.chat_service import ToolExecutor

    execu = ToolExecutor(db=MagicMock())  # no user_id -> anonymous
    execu.product_service = _FakePricedProductService(by_id={
        _PROD_A: {"id": _PROD_A, "name": "12mm Reinforcement Rod", "base_price": 8500.0,
                  "discount_price": 8200.0, "unit_of_measure": "length",
                  "minimum_order_quantity": 20},
    })

    import asyncio
    result = asyncio.run(execu._add_to_cart(_PROD_A, 20))

    assert result["staged"] is True
    assert result["guest"] is True
    assert result["quantity"] == 20
    assert result["price"] == 8200.0, "the discounted price is what the device cart must show"



# ── Cart claims must be backed by a tool result ─────────────────────────────

def test_system_prompt_requires_a_tool_call_before_cart_claims():
    """The prompt must forbid claiming a cart change without a tool result."""
    from app.services.chat_service import SYSTEM_PROMPT

    assert "you MUST call add_to_cart" in SYSTEM_PROMPT
    assert "without a confirming tool result" in SYSTEM_PROMPT
    assert "never pretend the add succeeded" in SYSTEM_PROMPT


def test_claims_cart_change_detects_the_fabricated_confirmation():
    """The exact reply from the transcript must be recognised as a claim."""
    from app.services.chat_service import _claims_cart_change

    assert _claims_cart_change(
        "10 pieces of Reinforcement bars (rebar) 12mm have been added to your cart. "
        "Do you want to proceed to checkout"
    )
    assert _claims_cart_change("I've added it to your cart.")
    assert _claims_cart_change("Your order has been placed.")


def test_claims_cart_change_ignores_harmless_wording():
    from app.services.chat_service import _claims_cart_change

    assert not _claims_cart_change(
        "Want me to add this to your cart? The minimum order is 20 lengths."
    )
    assert not _claims_cart_change("Cement is essential for foundations.")
    assert not _claims_cart_change("")


def test_has_cart_tool_result_only_counts_cart_tools():
    from app.services.chat_service import _has_cart_tool_result

    assert _has_cart_tool_result([{"tool": "add_to_cart", "result": {"success": True}}])
    assert _has_cart_tool_result([{"tool": "add_to_cart", "result": {"staged": True}}])
    assert not _has_cart_tool_result([{"tool": "search_products", "result": {"products": []}}])
    assert not _has_cart_tool_result([])


def _fake_chat_service(replies):
    """A ChatService whose AI returns the given texts, with persistence stubbed."""
    from app.services.chat_service import ChatService

    class _Msg:
        def __init__(self, content):
            self.content = content
            self.tool_calls = None

    class _Choice:
        def __init__(self, content):
            self.message = _Msg(content)

    class _Usage:
        total_tokens = 1
        prompt_tokens = 1
        completion_tokens = 1

    class _Response:
        def __init__(self, content):
            self.choices = [_Choice(content)]
            self.usage = _Usage()

    calls = {"n": 0, "messages": []}

    class _AI:
        async def chat_completion(self, messages=None, tools=None):
            if messages:
                calls["messages"].append(list(messages))
            text = replies[min(calls["n"], len(replies) - 1)]
            calls["n"] += 1
            return _Response(text)

    svc = ChatService(db=MagicMock(), is_authenticated=False, user_id=None)
    svc.ai_service = _AI()

    async def _no_history(*args, **kwargs):
        return {"messages": [], "metadata": []}

    svc._load_history = _no_history
    svc._save_history = _no_history
    return svc, calls


def test_fabricated_cart_claim_is_retried_after_a_corrective_nudge():
    """First reply claims a cart change with no tool call -> the model is re-asked."""
    svc, calls = _fake_chat_service([
        "10 pieces of Reinforcement bars (rebar) 12mm have been added to your cart. Do you want to proceed to checkout",
        "I can't add 10 lengths — the minimum order for 12mm Reinforcement Rod is 20 lengths.",
    ])

    import asyncio
    result = asyncio.run(svc.handle_message("add 10 pieces of 12mm reinforcement rods to the cart for me"))

    assert calls["n"] == 2, "the unbacked claim must trigger exactly one corrective round"
    # The corrective system nudge carries the MOQ reminder and the no-tool fact.
    nudge = calls["messages"][-1][-1]
    assert nudge["role"] == "system"
    assert "not called any tool" in nudge["content"]
    assert "minimum order quantity" in nudge["content"]

    assert result.reply.startswith("I can't add 10 lengths")
    assert "added to your cart" not in result.reply


def test_persistent_cart_claim_is_replaced_with_the_truth():
    """If the retry still claims a cart change, the shopper never sees the lie."""
    svc, calls = _fake_chat_service([
        "10 pieces have been added to your cart.",
        "10 pieces have been added to your cart.",
    ])

    import asyncio
    result = asyncio.run(svc.handle_message("add 10 pieces of 12mm reinforcement rods to the cart for me"))

    assert calls["n"] == 2, "retried once, then corrected"
    assert "added to your cart" not in result.reply
    assert "I haven't changed your cart yet" in result.reply


def test_legitimate_reply_is_returned_untouched():
    """A reply with no cart claim is never rewritten or retried."""
    svc, calls = _fake_chat_service(["Ordinary Portland Cement or PCC suits foundations."])

    import asyncio
    result = asyncio.run(svc.handle_message("what cement for foundation work?"))

    assert calls["n"] == 1
    assert result.reply == "Ordinary Portland Cement or PCC suits foundations."



# ── Multi-item cart requests must not be half-fulfilled ─────────────────────

def test_requested_add_target_count_counts_quantity_clauses():
    from app.services.chat_service import _requested_add_target_count

    assert _requested_add_target_count(
        "add 21 bags of 12mm rods for me and 10 bags of cement"
    ) == 2
    assert _requested_add_target_count("add 20 lengths of 12mm reinforcement rod") == 1
    assert _requested_add_target_count("I need 3 rolls of binding wire") == 1
    # Spec tokens must not masquerade as extra items.
    assert _requested_add_target_count("add 2 bags of 50kg cement") == 1
    assert _requested_add_target_count("add 4 sheets of 12mm plywood") == 1


def test_requested_add_target_count_ignores_prose_without_units():
    from app.services.chat_service import _requested_add_target_count

    assert _requested_add_target_count("add cement to my cart") == 0
    assert _requested_add_target_count("what cement for foundation work?") == 0
    assert _requested_add_target_count("") == 0


class _FakeToolCall:
    def __init__(self, name, arguments):
        self.id = f"{name}-1"
        self.type = "function"
        self.function = type("F", (), {"name": name, "arguments": arguments})()


def _multi_item_chat_service(responses, tool_results):
    """ChatService with scripted AI turns and a fake cart tool.

    `responses` is a list of ("text" | tool-call) per AI call:
      ("text", "...")                  -> final answer, no tool calls
      ("tool", name, args_json)        -> a function call that the loop executes
    """
    from app.services.chat_service import ChatService

    class _Msg:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class _Usage:
        total_tokens = 1
        prompt_tokens = 1
        completion_tokens = 1

    class _Response:
        def __init__(self, msg):
            self.choices = [type("C", (), {"message": msg})()]
            self.usage = _Usage()

    calls = {"n": 0, "messages": []}

    class _AI:
        async def chat_completion(self, messages=None, tools=None):
            if messages:
                calls["messages"].append(list(messages))
            spec = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            if spec[0] == "tool":
                return _Response(_Msg(None, [_FakeToolCall(spec[1], spec[2])]))
            return _Response(_Msg(spec[1]))

    class _Executor:
        def __init__(self):
            self.n = 0

        async def execute(self, name, args):
            result = tool_results[min(self.n, len(tool_results) - 1)]
            self.n += 1
            return result

    svc = ChatService(db=MagicMock(), is_authenticated=False, user_id=None)
    svc.ai_service = _AI()
    svc.tool_executor = _Executor()

    async def _history(*args, **kwargs):
        return {"messages": [], "metadata": []}

    svc._load_history = _history
    svc._save_history = _history

    async def _log(*args, **kwargs):
        return None

    svc._log_agent_turn = _log
    return svc, calls


_STAGED_CEMENT = {
    "success": False, "guest": True, "staged": True,
    "product_id": _PROD_A, "product_name": "Dangote Cement 50kg",
    "quantity": 10, "minimum_order_quantity": 10, "unit": "bag", "price": 12000.0,
}


def test_partial_multi_item_add_triggers_a_corrective_round():
    """One item added out of two requested -> the agent is told to finish the job."""
    svc, calls = _multi_item_chat_service(
        [
            ("tool", "add_to_cart", '{"product_id": "p", "quantity": 10}'),
            ("text", "10 bags of Dangote Cement 50kg have been saved to your cart on this device."),
            ("text", "21 lengths of 12mm Reinforcement Rod and 10 bags of Dangote Cement 50kg "
                     "are both in your cart."),
        ],
        [_STAGED_CEMENT],
    )

    import asyncio
    result = asyncio.run(svc.handle_message(
        "add 21 bags of 12mm rods for me and 10 bags of cement"
    ))

    assert calls["n"] == 3, "partial fulfilment must trigger exactly one corrective round"
    nudge = calls["messages"][-1][-1]
    assert nudge["role"] == "system"
    assert "listed 2 items" in nudge["content"]
    assert "only 1 were handled" in nudge["content"]
    assert "never stay" in nudge["content"]
    assert "12mm Reinforcement Rod" in result.reply


def test_single_item_request_never_triggers_the_multi_item_round():
    svc, calls = _multi_item_chat_service(
        [
            ("tool", "add_to_cart", '{"product_id": "p", "quantity": 20}'),
            ("text", "20 lengths of 12mm Reinforcement Rod saved to your cart on this device."),
        ],
        [_STAGED_CEMENT],
    )

    import asyncio
    result = asyncio.run(svc.handle_message("add 20 lengths of 12mm reinforcement rod"))

    assert calls["n"] == 2, "a single-item request is answered in one shot"
    assert "saved to your cart" in result.reply


def test_add_result_landed_distinguishes_staged_from_refused():
    from app.services.chat_service import _add_result_landed

    assert _add_result_landed({"success": True, "product_id": "p"})
    assert _add_result_landed({"success": False, "guest": True, "staged": True})
    assert not _add_result_landed({"success": False, "error": "Below minimum order quantity"})
    assert not _add_result_landed({})
    assert not _add_result_landed(None)


def test_failed_add_claimed_as_success_triggers_a_corrective_round():
    """The reply says the cement was saved, but that add_to_cart call failed."""
    svc, calls = _multi_item_chat_service(
        [
            ("tool", "add_to_cart", '{"product_id": "rods", "quantity": 21}'),
            ("tool", "add_to_cart", '{"product_id": "cement", "quantity": 10}'),
            ("text", "21 lengths of 12mm Reinforcement Rod have been saved to your cart on this "
                     "device, and 10 bags of Dangote Cement 50kg have been saved too."),
            ("text", "21 lengths of 12mm Reinforcement Rod were saved to your cart on this "
                     "device. I couldn't add the cement: the minimum order is 40 bags."),
        ],
        [
            {"success": False, "guest": True, "staged": True, "product_id": "rods",
             "product_name": "12mm Reinforcement Rod", "quantity": 21,
             "minimum_order_quantity": 20, "unit": "length", "price": 8200.0},
            {"success": False, "product_id": "cement",
             "product_name": "Dangote Cement 50kg", "quantity": 10,
             "error": "Below minimum order quantity (40 bags)"},
        ],
    )

    import asyncio
    result = asyncio.run(svc.handle_message(
        "add 21 bags of 12mm rods for me and 10 bags of cement"
    ))

    assert calls["n"] == 4, "a claimed-but-failed add must trigger exactly one corrective round"
    nudge = calls["messages"][-1][-1]
    assert nudge["role"] == "system"
    assert "did NOT take effect" in nudge["content"]
    assert "Below minimum order quantity (40 bags)" in nudge["content"]
    assert "minimum order is 40 bags" in result.reply
    assert "have been saved too" not in result.reply


def test_two_item_happy_path_confirms_both_within_the_turn_budget():
    """search+add for each of two items must complete without hitting the fallback."""
    rods = {"success": False, "guest": True, "staged": True, "product_id": "rods",
            "product_name": "12mm Reinforcement Rod", "quantity": 21,
            "minimum_order_quantity": 20, "unit": "length", "price": 8200.0}
    cement = {"success": False, "guest": True, "staged": True, "product_id": "cement",
              "product_name": "Dangote Cement 50kg", "quantity": 10,
              "minimum_order_quantity": 10, "unit": "bag", "price": 12000.0}
    svc, calls = _multi_item_chat_service(
        [
            ("tool", "search_products", '{"query": "12mm reinforcement rod"}'),
            ("tool", "add_to_cart", '{"product_id": "rods", "quantity": 21}'),
            ("tool", "search_products", '{"query": "cement"}'),
            ("tool", "add_to_cart", '{"product_id": "cement", "quantity": 10}'),
            ("text", "21 lengths of 12mm Reinforcement Rod have been saved to your cart on this "
                     "device, and 10 bags of Dangote Cement 50kg have been saved too."),
        ],
        [{"products": []}, rods, {"products": []}, cement],
    )

    import asyncio
    result = asyncio.run(svc.handle_message(
        "add 21 bags of 12mm rods for me and 10 bags of cement"
    ))

    assert calls["n"] == 5, "both items confirmed in one pass, no corrective round"
    assert "21 lengths" in result.reply
    assert "10 bags" in result.reply


def test_two_staged_guest_adds_emit_one_action_per_item():
    """`next(...)` kept only the first staged add, dropping every later item."""
    svc = _fake_chat_service([])[0]
    svc.is_authenticated = False
    rods = {"success": False, "guest": True, "staged": True, "product_id": _PROD_A,
            "product_name": "12mm Reinforcement Rod", "quantity": 21,
            "minimum_order_quantity": 20, "unit": "length", "price": 8200.0}
    cement = {"success": False, "guest": True, "staged": True, "product_id": "cement-1",
              "product_name": "Dangote Cement 50kg", "quantity": 10,
              "minimum_order_quantity": 10, "unit": "bag", "price": 12000.0}
    tool_results = [
        {"tool": "add_to_cart", "result": rods},
        {"tool": "add_to_cart", "result": cement},
    ]

    actions = svc._build_actions(
        "Both items have been saved to your cart on this device.",
        tool_results,
        "add 21 bags of 12mm rods for me and 10 bags of cement",
    )

    staged = [a for a in actions if a.type == "add_to_cart" and a.data.get("staged")]
    assert len(staged) == 2, "every staged item must reach the device cart"
    by_id = {a.data["product_id"]: a.data for a in staged}
    assert by_id[_PROD_A]["quantity"] == 21
    assert by_id[_PROD_A]["minimum_order_quantity"] == 20
    assert by_id["cement-1"]["quantity"] == 10
    assert by_id["cement-1"]["unit"] == "bag"
    assert by_id["cement-1"]["product_name"] == "Dangote Cement 50kg"


def test_duplicate_staged_add_for_one_product_is_not_written_twice():
    svc = _fake_chat_service([])[0]
    svc.is_authenticated = False
    base = {"success": False, "guest": True, "staged": True, "product_id": _PROD_A,
            "product_name": "12mm Reinforcement Rod", "quantity": 20,
            "minimum_order_quantity": 20, "unit": "length", "price": 8200.0}
    tool_results = [
        {"tool": "add_to_cart", "result": dict(base, quantity=20)},
        {"tool": "add_to_cart", "result": dict(base, quantity=20)},
    ]

    actions = svc._build_actions("Saved to your cart.", tool_results, "add 20 rods")

    staged = [a for a in actions if a.type == "add_to_cart" and a.data.get("staged")]
    assert len(staged) == 1, "a repeated add of the same product must not double-write"


def test_denial_containing_confirmation_words_is_not_a_claim():
    from app.services.chat_service import _claims_cart_change

    assert _claims_cart_change(
        "Nothing has been saved to your cart yet — the minimum order is 20 lengths."
    ) is False
    assert _claims_cart_change(
        "I couldn't add the cement, so 20 lengths saved to your cart is all that is there."
    ) is False
    # The guest/device phrasing IS a claim when it isn't denied.
    assert _claims_cart_change(
        "10 bags of Dangote Cement 50kg have been saved to your cart on this device."
    )


def test_multi_item_request_with_no_adds_is_left_to_the_claim_guard():
    """Zero adds + no cart claim is not a partial fill, so this guard stays quiet."""
    svc, calls = _multi_item_chat_service(
        [("text", "Both items are available; which brand of cement would you like?")],
        [_STAGED_CEMENT],
    )

    import asyncio
    result = asyncio.run(svc.handle_message(
        "add 21 bags of 12mm rods for me and 10 bags of cement"
    ))

    assert calls["n"] == 1
    assert "which brand of cement" in result.reply

