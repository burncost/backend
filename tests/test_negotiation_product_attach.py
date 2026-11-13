"""Vendor product attachment on a price offer.

Contract pinned here:
  * A vendor can only attach a product they own and that is still active.
  * The attachment stores an immutable snapshot, so the buyer still sees what was
    offered after the vendor edits (or deletes) the catalogue item.
  * Sending a price with the attachment makes it an acceptable offer
    (`final_price`), and the implied discount is measured off the list price.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.product import ProductStatus
from app.services.demand_broadcast_service import (
    _offer_dict,
    attach_product_to_negotiation,
    product_snapshot,
)

VENDOR_ID = uuid4()
OTHER_VENDOR_ID = uuid4()


class _FakeResult:
    def __init__(self, values):
        self._values = values if isinstance(values, list) else [values]

    def scalars(self):
        return self

    def first(self):
        return self._values[0] if self._values else None

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def all(self):
        return list(self._values)


class _FakeSession:
    def __init__(self, results=None):
        self._results = list(results or [])
        self.added = []

    async def execute(self, stmt):
        if not self._results:
            raise AssertionError("unexpected extra db.execute() call")
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)


def _product(vendor_id=VENDOR_ID, status=ProductStatus.ACTIVE):
    return SimpleNamespace(
        id=uuid4(),
        vendor_id=vendor_id,
        status=status,
        name="Sandcrete Block 225mm",
        sku="BLK-225",
        category_id=uuid4(),
        base_price=500,
        unit_of_measure="nr",
        minimum_order_quantity=100,
    )


def _negotiation():
    return SimpleNamespace(
        id=uuid4(),
        product_id=None,
        product_name="9-inch sandcrete block (225mm)",
        unit="nr",
        bid_price=None,
        status="counter_offered",
    )


def test_attach_product_snapshots_the_offer_and_proposes_its_price():
    product = _product()
    n = _negotiation()
    # product lookup, then the product's primary image
    db = _FakeSession([[product], ["https://cdn.example.com/block.jpg"]])

    offer = asyncio.run(
        attach_product_to_negotiation(
            db, n, vendor_id=VENDOR_ID, product_id=product.id, price=450, note="",
        )
    )

    assert n.product_id == product.id
    assert n.bid_price == 450
    assert n.unit == "nr"
    assert offer.final_price == 450
    assert offer.discount_percent == 10.0  # ₦450 against a ₦500 list price
    assert offer.product_id == product.id
    assert offer.product_snapshot["name"] == "Sandcrete Block 225mm"
    assert offer.product_snapshot["image_url"] == "https://cdn.example.com/block.jpg"


def test_attach_product_defaults_to_the_list_price():
    product = _product()
    n = _negotiation()
    db = _FakeSession([[product], []])

    offer = asyncio.run(
        attach_product_to_negotiation(db, n, vendor_id=VENDOR_ID, product_id=product.id)
    )

    assert offer.final_price == 500
    assert offer.discount_percent == 0


def test_attach_product_rejects_another_vendors_product():
    product = _product(vendor_id=OTHER_VENDOR_ID)
    db = _FakeSession([[product]])

    with pytest.raises(ValueError) as caught:
        asyncio.run(
            attach_product_to_negotiation(
                db, _negotiation(), vendor_id=VENDOR_ID, product_id=product.id
            )
        )

    assert "another vendor" in str(caught.value)


def test_attach_product_rejects_an_inactive_product():
    product = _product(status=ProductStatus.DRAFT)
    db = _FakeSession([[product]])

    with pytest.raises(ValueError) as caught:
        asyncio.run(
            attach_product_to_negotiation(
                db, _negotiation(), vendor_id=VENDOR_ID, product_id=product.id
            )
        )

    assert "not active" in str(caught.value)


def test_product_snapshot_is_plain_data():
    product = _product()
    snapshot = product_snapshot(product)

    assert snapshot["id"] == str(product.id)
    assert snapshot["base_price"] == 500.0
    assert snapshot["minimum_order_quantity"] == 100
    assert snapshot["unit"] == "nr"


def test_offer_dict_serializes_the_snapshot():
    product = _product()
    offer = SimpleNamespace(
        id=uuid4(),
        offered_by="supplier",
        discount_percent=10,
        final_price=450,
        note="ok",
        product_id=product.id,
        product_snapshot=product_snapshot(product),
        created_at=None,
    )

    payload = _offer_dict(offer)

    assert payload["final_price"] == 450.0
    assert payload["product"]["name"] == "Sandcrete Block 225mm"
    assert payload["product_id"] == str(product.id)
