"""BOQ → cart resolution.

Covers the promise made by the "Place order" button: every line goes to an
active (verified) vendor with enough stock, a well-rated supplier is preferred,
the cheapest landed cost wins inside that, quantities below an MOQ are raised
rather than refused, and a unit mismatch is reported instead of guessed.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.boq_cart_service import (
    ALTERNATES_PER_LINE,
    BOQCartService,
    PREFERRED_VENDOR_RATING,
)


def _run(coro):
    return asyncio.run(coro)


def _offer(**over):
    """A catalogue offer shaped like ToolExecutor._get_cheapest_price output."""
    offer = {
        "product_id": "p1",
        "product_name": "Dangote Cement 50kg",
        "rate": 8000.0,
        "base_price": 8000.0,
        "discount_price": None,
        "unit": "bag",
        "marketer": "BuildMart Ltd",
        "market": "Wuse",
        "minimum_order_quantity": 1,
        "shipping_fee": 0.0,
        "stock": 100,
        "status": "active",
        "rating": 4.5,
        "is_verified": True,
        "review_count": 12,
        "total_procurement_cost": 8000.0,
        "verified": True,
    }
    offer.update(over)
    return offer


def _service(offers, relaxed_from=None):
    service = BOQCartService(db=MagicMock())
    service.executor = MagicMock()
    service.executor._get_cheapest_price = AsyncMock(
        return_value={
            "offers": offers,
            "matched_query": "cement",
            "relaxed_from": relaxed_from,
        }
    )
    return service


def _boq(*items, element_name: str = "Substructure"):
    return {
        "project_info": {"city": "Abuja"},
        "elements": [{"element_name": element_name, "items": list(items)}],
    }


def _item(**over):
    item = {
        "item_code": "SUB-001",
        "description": "Ordinary Portland cement",
        "unit": "bag",
        "quantity": 20,
    }
    item.update(over)
    return item


# ── Vendor ranking ───────────────────────────────────────────────────────────

def test_well_rated_vendor_is_preferred_over_a_cheaper_unrated_one():
    service = _service(
        [
            _offer(product_id="cheap", rate=7000.0, rating=0, marketer="Cheap Co"),
            _offer(product_id="rated", rate=8000.0, rating=4.6, marketer="Rated Co"),
        ]
    )

    result = _run(service.resolve(_boq(_item())))

    line = result["lines"][0]
    assert line["match"]["product_id"] == "rated"
    assert line["match"]["rating"] >= PREFERRED_VENDOR_RATING
    # The cheaper offer is still returned as an alternate the shopper can pick.
    assert "cheap" in [a["product_id"] for a in line["alternates"]]


def test_cheapest_wins_among_equally_well_rated_vendors():
    service = _service(
        [
            _offer(product_id="pricey", rate=9000.0, rating=4.4),
            _offer(product_id="cheap", rate=7000.0, rating=4.1),
        ]
    )

    line = _run(service.resolve(_boq(_item())))["lines"][0]

    assert line["match"]["product_id"] == "cheap"
    assert line["line_total"] == pytest.approx(7000.0 * 20)


def test_lower_rated_fallback_is_used_and_labelled_when_nothing_else_exists():
    service = _service([_offer(product_id="only", rate=7000.0, rating=2.0)])

    line = _run(service.resolve(_boq(_item())))["lines"][0]

    assert line["match"]["product_id"] == "only"
    assert any("3.5+" in reason for reason in line["reasons"])


def test_offers_include_up_to_three_alternates():
    service = _service([_offer(product_id=f"p{i}", rate=7000.0 + i) for i in range(6)])

    line = _run(service.resolve(_boq(_item())))["lines"][0]

    assert len(line["alternates"]) == ALTERNATES_PER_LINE


# ── Gating: verified, in stock, active ───────────────────────────────────────

def test_only_verified_vendors_are_asked_for():
    service = _service([_offer()])

    _run(service.resolve(_boq(_item())))

    assert service.executor._get_cheapest_price.await_args.kwargs["only_verified"] is True


def test_out_of_stock_offer_is_rejected():
    service = _service([_offer(stock=5)])

    result = _run(service.resolve(_boq(_item(quantity=20))))

    assert result["lines"] == []
    assert "out of stock" in result["unmatched"][0]["reason"]


def test_inactive_listing_is_not_buyable():
    service = _service([_offer(status="out_of_stock")])

    result = _run(service.resolve(_boq(_item())))

    assert result["lines"] == []
    assert "no active" in result["unmatched"][0]["reason"]


def test_unit_mismatch_is_reported_not_guessed():
    # The bill measures concrete in m3, the only listing sells it by the bag.
    service = _service([_offer(unit="bag")])

    result = _run(service.resolve(_boq(_item(description="Concrete 1:2:4", unit="m3"))))

    assert result["lines"] == []
    assert "different unit" in result["unmatched"][0]["reason"]


def test_m2_walling_converts_to_blocks_with_the_documented_factor():
    service = _service([_offer(product_id="blk", unit="nr", rate=500.0, stock=100000)])

    line = _run(
        service.resolve(
            _boq(
                _item(
                    description="Blockwork in 225mm sandcrete wall",
                    unit="m2",
                    quantity=100,
                )
            )
        )
    )["lines"][0]

    # 100 m2 x 9.9 blocks/m2 = 990 blocks.
    assert line["match"]["billable_quantity"] == 990
    assert line["line_total"] == pytest.approx(990 * 500.0)


# ── MOQ handling ─────────────────────────────────────────────────────────────

def test_quantity_below_supplier_minimum_is_raised_not_refused():
    service = _service([_offer(minimum_order_quantity=50, rate=100.0, stock=500)])

    result = _run(service.resolve(_boq(_item(quantity=10))))
    line = result["lines"][0]

    assert line["match"]["billable_quantity"] == 50
    assert any("supplier minimum" in reason for reason in line["reasons"])
    assert any("minimum order quantity" in note for note in result["summary"]["notes"])


def test_line_total_includes_shipping():
    service = _service([_offer(rate=1000.0, shipping_fee=2500.0)])

    line = _run(service.resolve(_boq(_item(quantity=3))))["lines"][0]

    assert line["line_total"] == pytest.approx(3 * 1000.0 + 2500.0)


# ── What is not a cart line ──────────────────────────────────────────────────

def test_preliminaries_and_allowances_are_skipped():
    service = _service([_offer()])
    boq = {
        "project_info": {"city": "Abuja"},
        "elements": [
            {
                "element_name": "Preliminaries",
                "items": [_item(item_code="PRE-001", description="Site insurance")],
            },
            {
                "element_name": "Substructure",
                "items": [
                    _item(item_code="SUB-002", description="Provisional sum for piling"),
                    _item(item_code="SUB-003", description="Supervision of works"),
                ],
            },
        ],
    }

    result = _run(service.resolve(boq))

    assert result["lines"] == []
    assert len(result["skipped"]) == 3
    assert "preliminaries" in result["skipped"][0]["reason"]


def test_bill_wording_is_stripped_from_the_search_term():
    assert (
        BOQCartService._search_term("Provide and fix Ceramic floor tiles (600x600mm)")
        == "Ceramic floor tiles"
    )
    assert (
        BOQCartService._search_term("Supply and install 9-inch sandcrete blocks")
        == "9-inch sandcrete blocks"
    )


def test_summary_reports_suppliers_and_totals():
    service = _service(
        [
            _offer(marketer="BuildMart Ltd", rate=1000.0),
            _offer(product_id="p2", marketer="Cement King", rate=1200.0, rating=4.9),
        ]
    )

    summary = _run(service.resolve(_boq(_item(quantity=4))))["summary"]

    assert summary["lines_ready"] == 1
    assert summary["lines_unmatched"] == 0
    # BuildMart and Cement King both clear the 3.5★ gate, so the cheaper
    # 1 000.00 offer wins and names the supplier on the summary.
    assert summary["suppliers"] == ["BuildMart Ltd"]
    assert summary["materials_subtotal"] == pytest.approx(4 * 1000.0)
