"""Pricing guards that keep a BOQ's contract sum realistic.

  1. Unit reconciliation: the catalogue sells steel by the 12 m bar ("length")
     or the tonne while a Nigerian bill measures it in kg. Stamping a per-bar
     rate onto a kg quantity was the biggest single cause of an inflated total.
  2. Double-counting: the same material + quantity billed under two elements is
     dropped rather than priced twice.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from app.services.boq_generator import BOQGenerator
from app.services.price_service import (
    PriceService,
    rebar_bar_mass_kg,
    unit_reconciliation_factor,
)

CITY = "Abuja"


def _run(coro):
    return asyncio.run(coro)


# ── 1. Unit reconciliation ───────────────────────────────────────────────────

def test_rebar_bar_mass_reads_the_nominal_diameter():
    assert rebar_bar_mass_kg("High yield reinforcement Y12 bars") == 10.65
    assert rebar_bar_mass_kg("Y16 rebar") == 18.96
    assert rebar_bar_mass_kg("rebar without a size") is None


def test_factor_is_one_when_the_units_already_agree():
    assert unit_reconciliation_factor("bag", "bag") == 1.0
    assert unit_reconciliation_factor("kg", "") == 1.0


def test_factor_converts_a_per_bar_rate_to_a_per_kilogram_rate():
    # One 18.96 kg bar -> price per kg is the bar price divided by its mass.
    factor = unit_reconciliation_factor("kg", "length", "Y16 rebar")
    assert factor is not None
    assert abs(factor - 1 / 18.96) < 1e-9


def test_factor_converts_tonne_to_kilogram():
    # A per-tonne price -> per kg is one thousandth.
    assert unit_reconciliation_factor("kg", "tonne") == 1 / 1000


def test_factor_is_none_for_units_that_measure_different_things():
    assert unit_reconciliation_factor("m3", "bag") is None


def test_match_item_prices_a_per_bar_rebar_rate_per_kilogram():
    """A per-length steel price must be spread over the bar's mass, not applied
    to every kilogram."""
    service = PriceService()
    catalogue = {
        "rate": 18_000.0,
        "unit": "length",  # one 12 m bar
        "product_name": "Y16 High yield rebar 12m",
        "product_code": "REB-016",
        "price_source": "product_catalogue",
        "product_id": "44444444-4444-4444-4444-444444444444",
        "base_price": 18_000.0,
        "discount_price": None,
        "in_stock": 100,
        "price_basis": "Vendor catalogue price",
    }
    item = {
        "item_code": "SUB-06",
        "description": "High yield reinforcement Y16 bars (12m)",
        "quantity": 1000.0,
        "unit": "kg",
        "adjusted_rate": 0,
    }
    with patch.object(service, "_load_prices", AsyncMock(return_value={})), patch.object(
        service, "_search_by_keywords", AsyncMock(return_value=None)
    ), patch.object(service, "get_catalogue_rate", AsyncMock(return_value=catalogue)):
        enriched, _discrepancy, _oos = _run(service._match_item(item, CITY))

    expected_rate = round(18_000.0 / 18.96, 2)
    assert enriched["adjusted_rate"] == expected_rate
    assert enriched["amount"] == round(1000.0 * expected_rate, 2)
    assert expected_rate < 18_000.0  # never the raw per-bar figure


def test_match_item_keeps_the_line_rate_when_units_are_incompatible():
    service = PriceService()
    catalogue = {
        "rate": 9_000.0,
        "unit": "bag",
        "product_name": "Cement",
        "product_code": "CEM-001",
        "price_source": "product_catalogue",
        "product_id": "55555555-5555-5555-5555-555555555555",
        "base_price": 9_000.0,
        "discount_price": None,
        "in_stock": 100,
        "price_basis": "Vendor catalogue price",
    }
    item = {
        "item_code": "CON-01",
        "description": "Concrete in foundation",
        "quantity": 5.0,
        "unit": "m3",
        "adjusted_rate": 5_000.0,
    }
    with patch.object(service, "_load_prices", AsyncMock(return_value={})), patch.object(
        service, "_search_by_keywords", AsyncMock(return_value=None)
    ), patch.object(service, "get_catalogue_rate", AsyncMock(return_value=catalogue)):
        enriched, discrepancy, _oos = _run(service._match_item(item, CITY))

    assert enriched["adjusted_rate"] == 5_000.0  # the catalogue rate was not stamped
    assert enriched["unit_mismatch"] == {"bill_unit": "m3", "catalogue_unit": "bag"}
    assert discrepancy["action"] == "skipped_incompatible_unit"


# ── 2. Double-counting guard ─────────────────────────────────────────────────

def test_same_material_and_quantity_is_not_billed_under_two_elements():
    boq = {
        "elements": [
            {"elementName": "SUBSTRUCTURE", "items": [
                {"itemCode": "SUB-06", "description": "High yield reinforcement",
                 "quantity": 4018, "unit": "kg"},
            ]},
            {"elementName": "SUPERSTRUCTURE", "items": [
                {"itemCode": "SUP-03", "description": "High yield reinforcement",
                 "quantity": 4018, "unit": "kg"},
            ]},
        ],
    }
    normalised = BOQGenerator._normalise_ai_boq(boq)
    assert len(normalised["elements"][0]["items"]) == 1
    assert normalised["elements"][1]["items"] == []


def test_lines_with_different_quantities_are_both_kept():
    boq = {
        "elements": [
            {"elementName": "A", "items": [
                {"description": "Excavate trench", "quantity": 10, "unit": "m3"},
            ]},
            {"elementName": "B", "items": [
                {"description": "Excavate trench", "quantity": 25, "unit": "m3"},
            ]},
        ],
    }
    normalised = BOQGenerator._normalise_ai_boq(boq)
    assert len(normalised["elements"][0]["items"]) == 1
    assert len(normalised["elements"][1]["items"]) == 1
