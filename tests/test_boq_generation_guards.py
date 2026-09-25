"""Generation guards: incomplete AI bills, false ₦0 prices, guest masking, totals.

These cover the defects behind the reported anonymous-preview output: every line
showing ₦0 next to a two-element, ₦2m "complete" contract sum drawn from a
12.96 m² extraction.
"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.services.boq_generator import (
    MIN_AI_ELEMENT_COVERAGE,
    REQUIRED_AI_ELEMENTS,
    BOQGenerator,
)
from app.services.drawing_to_boq_mapper import DrawingToBOQMapper
from app.services.price_service import PriceService


def _run(coro):
    return asyncio.run(coro)


def _ai_boq(*element_names, items_per_element: int = 2) -> dict:
    """Build a camelCase BOQ shaped like a raw Gemini response."""
    return {
        "projectTitle": "Test",
        "elements": [
            {
                "elementName": name,
                "items": [
                    {
                        "itemCode": f"{name[:3].upper()}-{i + 1}",
                        "description": f"{name} item {i + 1}",
                        "quantity": 10.0,
                        "unit": "m2",
                        "rate": 0,
                        "amount": 0,
                    }
                    for i in range(items_per_element)
                ],
            }
            for name in element_names
        ],
    }


# ── AI response handling ─────────────────────────────────────────────────────

def test_ai_camel_case_keys_are_folded_to_snake_case():
    boq = BOQGenerator._normalise_ai_boq(_ai_boq("Preliminaries", "Substructure"))

    assert [el["element_name"] for el in boq["elements"]] == ["Preliminaries", "Substructure"]
    assert boq["elements"][0]["items"][0]["item_code"] == "PRE-1"


def test_truncated_ai_boq_fails_the_element_coverage_gate():
    # The reported case: the model was cut off after Substructure, yet
    # json_repair still returned syntactically valid JSON so nothing objected.
    boq = BOQGenerator._normalise_ai_boq(_ai_boq("Preliminaries", "Substructure"))
    missing = BOQGenerator._missing_element_groups(boq)

    assert len(missing) == len(REQUIRED_AI_ELEMENTS) - 2
    assert len(REQUIRED_AI_ELEMENTS) - len(missing) < MIN_AI_ELEMENT_COVERAGE


def test_complete_ai_boq_passes_the_coverage_gate():
    boq = BOQGenerator._normalise_ai_boq(_ai_boq(*REQUIRED_AI_ELEMENTS))

    assert BOQGenerator._missing_element_groups(boq) == []
    assert len(REQUIRED_AI_ELEMENTS) >= MIN_AI_ELEMENT_COVERAGE


class _Candidate:
    finish_reason = "FinishReason.MAX_TOKENS"


class _Response:
    candidates = [_Candidate()]


def test_token_limit_finish_reason_is_detected():
    assert "MAX_TOKENS" in BOQGenerator._finish_reason(_Response())
    assert BOQGenerator._finish_reason(object()) == ""


def test_parse_boq_json_accepts_fenced_and_bare_payloads():
    payload = {"elements": []}

    assert BOQGenerator._parse_boq_json(f"```json\n{json.dumps(payload)}\n```") == payload
    assert BOQGenerator._parse_boq_json(f"prefix {json.dumps(payload)} suffix") == payload
    assert BOQGenerator._parse_boq_json("no json here at all") is None


# ── Pricing: an absent price is never ₦0 ─────────────────────────────────────

def test_unpriced_item_is_flagged_not_priced_at_zero():
    service = PriceService()
    service._load_prices = AsyncMock(return_value={})
    service.engine.get_market_rate_estimate = AsyncMock(return_value=None)

    item = {
        "item_code": "PRE-001",
        "description": "Site clearance, including removal of shrubs",
        "quantity": 12.96,
        "unit": "m2",
        "rate": 0,
    }
    enriched, _discrepancy, out_of_stock = _run(service._match_item(item, "Abuja"))

    assert enriched["rate"] is None
    assert enriched["adjusted_rate"] is None
    assert enriched["price_status"] == "unavailable"
    assert enriched["rate_source"] == "unavailable"
    assert enriched["out_of_stock"] is True
    assert out_of_stock is not None


def test_totals_chain_carries_contingency_overheads_and_vat():
    service = PriceService()
    elements = [{"items": [{"amount": 600000.0}, {"amount": 400000.0}]}]

    totals = service.recalculate_totals(elements)

    assert totals["sub_total"] == 1_000_000.0
    assert totals["contingency_pct"] == 10.0
    assert totals["contingency_amount"] == 100_000.0
    assert totals["overheads_profit_pct"] == 10.0
    assert totals["overheads_profit_amount"] == 100_000.0
    assert totals["vat_amount"] == pytest.approx(90_000.0)
    assert totals["total_contract_sum"] == pytest.approx(1_290_000.0)
    assert elements[0]["element_total"] == 1_000_000.0


# ── Guest payload: mask consistently, and show one real price ────────────────

def _guest_fixture() -> dict:
    return {
        "_id": "abc123",
        "elements": [
            {
                "element_name": "Preliminaries",
                "element_total": 2345678.0,
                "cost_percentage_of_total": 41.2,
                "items": [
                    {
                        "item_code": f"PRE-{i + 1}",
                        "description": f"Preliminary item {i + 1}",
                        "quantity": 1,
                        "unit": "nr",
                        "adjusted_rate": 12345.0,
                        "rate": 0,
                        "amount": 12345.0,
                    }
                    for i in range(5)
                ],
            }
        ],
        "summary": {
            "sub_total": 2345678.0,
            "contingency": 234567.8,
            "contingency_pct": 10.0,
            "overheads_profit": 234567.8,
            "overheads_profit_pct": 10.0,
            "vat": 211000.0,
            "vat_pct": 7.5,
            "total_contract_sum": 3025000.0,
            "cost_per_m2": 25223.0,
            "cost_scenarios": {"low": 2722500.0, "expected": 3025000.0, "high": 3327500.0},
        },
    }


def test_guest_payload_masks_every_derived_money_figure():
    from app.api.v1.endpoints.boqs import _truncate_boq_for_guest

    result = _truncate_boq_for_guest(_guest_fixture())
    summary = result["summary"]

    # Every figure that recovers the bill must be masked, not just the headline.
    assert summary["sub_total"] == 2_000_000.0
    assert summary["contingency"] == 200_000.0
    assert summary["vat"] == 200_000.0
    assert summary["cost_per_m2"] == 20_000.0
    assert summary["cost_scenarios"] == {
        "low": 2_000_000.0,
        "expected": 3_000_000.0,
        "high": 3_000_000.0,
    }
    assert result["elements"][0]["element_total"] == 2_000_000.0
    assert "cost_percentage_of_total" not in result["elements"][0]

    assert result["guest_preview"] == {
        "masked": True,
        "elements_total": 1,
        "items_total": 5,
        "items_shown": 3,
        "unpriced_items": 0,
    }


def test_guest_payload_surfaces_one_priced_line_when_all_visible_are_unpriced():
    from app.api.v1.endpoints.boqs import _truncate_boq_for_guest

    unpriced = {
        "rate": None,
        "adjusted_rate": None,
        "price_status": "unavailable",
        "quantity": 1,
    }
    full = {
        "elements": [
            {
                "element_name": "Preliminaries",
                "items": [
                    {"item_code": "PRE-001", "description": "Site clearance", **unpriced},
                    {"item_code": "PRE-002", "description": "Setting out", **unpriced},
                    {"item_code": "PRE-003", "description": "Insurance", **unpriced},
                    {
                        "item_code": "CON-001",
                        "description": "Concrete 1:2:4",
                        "quantity": 10,
                        "unit": "m3",
                        "adjusted_rate": 85000.0,
                        "rate": 0,
                        "amount": 850000.0,
                    },
                    {
                        "item_code": "CON-002",
                        "description": "Blinding",
                        "quantity": 5,
                        "unit": "m3",
                        "adjusted_rate": 60000.0,
                        "rate": 0,
                        "amount": 300000.0,
                    },
                ],
            }
        ],
        "summary": {"sub_total": 1150000.0},
    }

    result = _truncate_boq_for_guest(full)
    shown = result["elements"][0]["items"]

    assert shown[0]["item_code"] == "CON-001", "the one priced line must be visible"
    assert shown[0]["adjusted_rate"] == 85000.0
    assert shown[-1]["item_code"] == "..."
    assert len([i for i in shown if i["item_code"] != "..."]) == 3
    assert result["guest_preview"]["unpriced_items"] == 3


# ── Drawing geometry: implausible areas cannot pass as 90% confidence ────────

def test_mapper_rejects_implausibly_small_extracted_area():
    # The reported case: a whole floor plan read as 12.96 m² with no room
    # dimensions, billed as a two-element "complete" BOQ.
    result = DrawingToBOQMapper().map(
        extracted_geometry={"floor_area_m2": 12.96, "rooms": []},
        project_meta={"building_type": "residential"},
    )

    assert result["needs_manual_fallback"] is True
    assert result["confidence"] <= 0.3
    assert result["raw_area_m2"] == 12.96
    assert result["gross_area_m2"] == 14.26
    assert "implausibly small" in result["fallback_reason"]
    assert result["geometry_warnings"][0] == result["fallback_reason"]


def test_mapper_accepts_a_credible_floor_plan_and_uplifts_the_area():
    result = DrawingToBOQMapper().map(
        extracted_geometry={
            "floor_area_m2": 120.0,
            "rooms": [
                {"name": "Living room", "area_m2": 30.0, "perimeter_m": 24.0},
                {"name": "Bedroom 1", "area_m2": 90.0, "perimeter_m": 44.0},
            ],
        },
        project_meta={"building_type": "residential"},
    )
    floor = result["request"].floors[0]

    assert result["needs_manual_fallback"] is False
    assert result["confidence"] == 0.6
    assert result["geometry_warnings"] == []
    assert result["raw_area_m2"] == 120.0
    assert result["gross_area_m2"] == 132.0
    assert floor.floor_area_m2 == 132.0
    assert floor.perimeter_m == 68.0


def test_mapper_defaults_undimensioned_rooms():
    result = DrawingToBOQMapper().map(
        extracted_geometry={
            "floor_area_m2": 120.0,
            "rooms": [
                {"name": "Living room", "area_m2": 30.0, "perimeter_m": 24.0},
                {"name": "Bedroom 1", "area_m2": 0, "perimeter_m": 0},
            ],
        },
        project_meta={"building_type": "residential"},
    )
    bedroom = next(r for r in result["request"].floors[0].rooms if r.name == "Bedroom 1")

    assert bedroom.area_m2 == 12.0
    assert any("default room sizes" in w for w in result["geometry_warnings"])
