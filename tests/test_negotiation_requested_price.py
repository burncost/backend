"""Buyer bids a naira figure - the backend derives the discount percentage.

Contract pinned here:
  * `requested_price` is validated against the product's catalogue `base_price`:
    a bid at or above the list price, or <= 0, or for a product that no longer
    exists, is a 400 - never a stored request.
  * `requested_discount` is derived server-side (2dp) so the buyer never does the
    maths, and the line `value` falls back to `bid * quantity`.
  * A lone `requested_discount` still behaves exactly as before (legacy callers),
    stored with `requested_price = NULL`.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.negotiations import create_negotiation, list_my_negotiations
from app.models.negotiation import Negotiation
from app.models.notification import Notification

BUILDER_ID = uuid4()
SUPPLIER_ID = uuid4()
USER_ID = uuid4()
PRODUCT_ID = uuid4()


# ── test doubles ─────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, values):
        self._values = values if isinstance(values, list) else [values]

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalar_one(self):
        return self._values[0]

    def scalar(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _FakeSession:
    """Scripted async session: each execute() pops the next scripted result.

    Call order inside `create_negotiation` for a bid: vendor, catalogue base
    price, negotiation count, then the re-fetch of the created row.
    """

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        if not self._results:
            raise AssertionError("unexpected extra db.execute() call")
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        return None


def _neg(**overrides):
    base = dict(
        id=uuid4(),
        negotiation_number="NEG-TEST-BID",
        builder_id=BUILDER_ID,
        supplier_id=SUPPLIER_ID,
        product_id=PRODUCT_ID,
        supplier=SimpleNamespace(business_name="Acme Supplies"),
        product_name="Cement 50kg",
        category="Cement",
        quantity=20,
        unit="bag",
        requested_discount=15.0,
        requested_price=850.0,
        counter_offer=None,
        final_discount=None,
        value=17000.0,
        status="pending",
        created_at=None,
        agreed_price=None,
        agreed_at=None,
        agreed_by=None,
        counter_offers=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _supplier():
    return SimpleNamespace(id=SUPPLIER_ID, user_id=USER_ID, business_name="Acme Supplies")


def _builder():
    return SimpleNamespace(id=BUILDER_ID, role="customer", email="builder@example.com")


def _bid(**overrides):
    payload = {
        "supplier_id": str(SUPPLIER_ID),
        "product_id": str(PRODUCT_ID),
        "product_name": "Cement 50kg",
        "category": "Cement",
        "quantity": 20,
        "unit": "bag",
        "requested_price": 850,
    }
    payload.update(overrides)
    return payload


def _created(db):
    rows = [x for x in db.added if isinstance(x, Negotiation)]
    assert len(rows) == 1
    return rows[0]



# ── the bid is translated into a percentage, server-side ─────────────────────

def test_bid_price_derives_discount_and_line_value():
    """₦850 against a ₦1,000 list price is a 15% request - computed by us."""
    row = _neg()
    db = _FakeSession([_supplier(), 1000, 1, row])  # vendor, base price, count, re-fetch

    result = asyncio.run(
        create_negotiation(payload=_bid(), current_user=_builder(), db=db)
    )

    created = _created(db)
    assert float(created.requested_price) == 850.0
    assert float(created.requested_discount) == 15.0
    # Buyers send a unit bid only: the line value is our maths too.
    assert float(created.value) == 17000.0
    # And the row the API returns carries the absolute bid back to the buyer.
    assert result["requested_price"] == 850.0
    assert result["requested_discount"] == 15.0


def test_bid_notifies_the_supplier_with_the_naira_figure():
    """The vendor must see the bid and the list price, not a bare percentage."""
    db = _FakeSession([_supplier(), 1000, 1, _neg()])

    asyncio.run(create_negotiation(payload=_bid(), current_user=_builder(), db=db))

    notes = [x for x in db.added if isinstance(x, Notification)]
    assert len(notes) == 1
    assert notes[0].user_id == USER_ID
    assert notes[0].link == "/supplier-dashboard"
    assert "builder@example.com" in notes[0].message
    assert "NGN 850.00/unit" in notes[0].message
    assert "15%" in notes[0].message
    assert "NGN 1,000.00 list price" in notes[0].message


def test_bid_rounds_the_derived_percentage_to_two_decimals():
    """1,000 -> 875 is 12.5%, kept at the 2dp every other money figure uses."""


# ── guard rails ──────────────────────────────────────────────────────────────

def test_bid_at_or_above_the_list_price_is_rejected():
    """A "discount" that is not a discount never reaches the supplier."""
    db = _FakeSession([_supplier(), 1000])

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            create_negotiation(payload=_bid(requested_price=1000), current_user=_builder(), db=db)
        )

    assert caught.value.status_code == 400
    assert "below the supplier's" in caught.value.detail
    assert [x for x in db.added if isinstance(x, Negotiation)] == []


def test_bid_must_be_greater_than_zero():
    db = _FakeSession([_supplier(), 1000])

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            create_negotiation(payload=_bid(requested_price=0), current_user=_builder(), db=db)
        )

    assert caught.value.status_code == 400
    assert "greater than 0" in caught.value.detail


def test_bid_without_a_product_id_is_rejected():
    """Without a product we cannot know the list price, so we cannot check it."""
    db = _FakeSession([_supplier()])

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            create_negotiation(payload=_bid(product_id=None), current_user=_builder(), db=db)
        )

    assert caught.value.status_code == 400
    assert "product_id" in caught.value.detail


def test_bid_for_an_unknown_product_is_rejected():
    """A delisted product degrades to a clear 400, not a bogus percentage."""
    db = _FakeSession([_supplier(), None])

    with pytest.raises(HTTPException) as caught:
        asyncio.run(create_negotiation(payload=_bid(), current_user=_builder(), db=db))

    assert caught.value.status_code == 400
    assert "not in the catalogue" in caught.value.detail


def test_percentage_only_request_still_works():
    """Legacy/percentage callers keep the old behaviour and the old wording."""
    row = _neg(requested_discount=7.5, requested_price=None)
    db = _FakeSession([_supplier(), 1, row])

    result = asyncio.run(
        create_negotiation(
            payload={
                "supplier_id": str(SUPPLIER_ID),
                "product_name": "Cement 50kg",
                "requested_discount": 7.5,
                "quantity": 10,
            },
            current_user=_builder(),
            db=db,
        )
    )

    created = _created(db)
    assert float(created.requested_discount) == 7.5
    assert created.requested_price is None
    notes = [x for x in db.added if isinstance(x, Notification)]
    assert "requested a 7.5% discount on" in notes[0].message
    assert result["requested_price"] is None

    db = _FakeSession([_supplier(), 1000, 1, _neg()])

    asyncio.run(
        create_negotiation(payload=_bid(requested_price=875), current_user=_builder(), db=db)
    )

    assert float(_created(db).requested_discount) == 12.5


# ── regression: listing must eager-load what `_neg_dict` reads ───────────────

class _EagerAwareSession(_FakeSession):
    """Mimics SQLAlchemy's async session: an un-loaded relation is fatal.

    `_neg_dict` reads `negotiation.supplier` and `.counter_offers`; a lazy load
    there raises `MissingGreenlet` (HTTP 500). This double only hands back those
    relations when the endpoint's SELECT asked for them (`selectinload` shows up
    as loader options), so a handler that forgets them fails loudly here.
    """

    async def execute(self, stmt):
        result = await super().execute(stmt)
        if getattr(stmt, "_with_options", ()):
            for row in result.all():
                row.supplier = SimpleNamespace(business_name="Acme Supplies")
                row.counter_offers = []
        return result


def test_list_eager_loads_the_relations_the_serializer_reads():
    """Regression: `GET /negotiations/` returned 500 MissingGreenlet because the
    row was loaded bare and `_neg_dict` lazy-loaded `supplier`/`counter_offers`.
    Both branches must carry the same loader options as every other caller."""
    bare = _neg(supplier=None, counter_offers=None)
    del bare.supplier, bare.counter_offers
    # vendor lookup, the rows, then one list-price lookup for the whole page
    db = _EagerAwareSession([None, [bare], [(PRODUCT_ID, 1000)]])

    result = asyncio.run(list_my_negotiations(current_user=_builder(), db=db))

    assert len(result["negotiations"]) == 1
    assert result["negotiations"][0]["supplier"] == "Acme Supplies"
    assert result["negotiations"][0]["requested_price"] == 850.0
