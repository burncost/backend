"""Vendor identity + negotiation opt-in on cart lines.

Contract pinned here:
  * Every cart line carries the real vendor business name (the historical
    "Verified Supplier" placeholder only survives as a fallback).
  * A line only reports `vendor_open_to_negotiate` when that line's own vendor
    opted in - it is a per-vendor flag, never a cart-wide one.
  * Vendors are resolved in a single batched query, and a product with no
    resolvable vendor degrades to no flag / no vendor_id instead of raising.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.endpoints.cart import get_cart
from app.schemas.cart import CartItemResponse

BUILDER_ID = uuid4()
VENDOR_ID = uuid4()
OTHER_VENDOR_ID = uuid4()
PRODUCT_A = uuid4()
PRODUCT_B = uuid4()


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

    Call order inside `get_cart` is fixed: cart items, negotiated lines (only when
    a line carries a `negotiation_id`), the batched vendor lookup, then per-line
    product / image lookups.
    """

    def __init__(self, results):
        self._results = list(results)
        self.executed = 0

    async def execute(self, stmt):
        self.executed += 1
        if not self._results:
            raise AssertionError("unexpected extra db.execute() call")
        return _FakeResult(self._results.pop(0))


def _cart_item(product_id, quantity=10, price=8000.0, negotiation_id=None):
    return SimpleNamespace(
        id=uuid4(),
        product_id=product_id,
        quantity=quantity,
        price_at_addition=price,
        negotiation_id=negotiation_id,
    )


def _product(product_id, name="Dangote Cement 50kg", category="Cement"):
    return SimpleNamespace(
        id=product_id,
        name=name,
        base_price=8500.0,
        discount_price=None,
        minimum_order_quantity=10,
        quantity=500,
        unit_of_measure="bag",
        category=SimpleNamespace(name=category),
        # The cart response exposes the category id for promo scope checks.
        category_id=uuid4(),
        is_verified=True,
    )


def _image(url="https://cdn.example/cement.jpg"):
    return SimpleNamespace(image_url=url)


def _builder():
    return SimpleNamespace(id=BUILDER_ID, role="customer")


# ── tests ────────────────────────────────────────────────────────────────────

def test_line_exposes_real_vendor_name_and_negotiation_opt_in():
    """An opted-in vendor's line names the supplier and offers negotiation."""
    item = _cart_item(PRODUCT_A)
    db = _FakeSession([
        [item],
        [(PRODUCT_A, VENDOR_ID, "Dangote Cement Plc", True)],
        _product(PRODUCT_A),
        _image(),
    ])

    cart = asyncio.run(get_cart(current_user=_builder(), db=db))
    line = cart["items"][0]

    assert line["supplier_name"] == "Dangote Cement Plc"
    assert line["vendor_id"] == str(VENDOR_ID)
    assert line["vendor_open_to_negotiate"] is True
    # The response contract still accepts the line (fields are declared).
    assert CartItemResponse(**line).vendor_open_to_negotiate is True
    # One execute per: items, vendors, product, image. A cart with no negotiated
    # line never runs the agreement lookup.
    assert db.executed == 4


def test_opt_in_is_per_vendor_not_cart_wide():
    """One opted-in vendor must not light up another vendor's line."""
    items = [_cart_item(PRODUCT_A), _cart_item(PRODUCT_B, price=3000.0)]
    db = _FakeSession([
        items,
        [
            (PRODUCT_A, VENDOR_ID, "Dangote Cement Plc", True),
            (PRODUCT_B, OTHER_VENDOR_ID, "BUA Supplies", False),
        ],
        _product(PRODUCT_A),
        _image(),
        _product(PRODUCT_B, name="BUA Cement 50kg"),
        _image("https://cdn.example/bua.jpg"),
    ])

    cart = asyncio.run(get_cart(current_user=_builder(), db=db))
    by_product = {line["product_id"]: line for line in cart["items"]}

    assert by_product[str(PRODUCT_A)]["vendor_open_to_negotiate"] is True
    assert by_product[str(PRODUCT_A)]["supplier_name"] == "Dangote Cement Plc"
    assert by_product[str(PRODUCT_B)]["vendor_open_to_negotiate"] is False
    assert by_product[str(PRODUCT_B)]["vendor_id"] == str(OTHER_VENDOR_ID)
    assert by_product[str(PRODUCT_B)]["supplier_name"] == "BUA Supplies"


def test_missing_vendor_degrades_to_placeholder_and_no_flag():
    """An unresolvable vendor yields the old placeholder and no negotiate offer."""
    item = _cart_item(PRODUCT_A)
    db = _FakeSession([
        [item],
        [],
        _product(PRODUCT_A),
        _image(),
    ])

    cart = asyncio.run(get_cart(current_user=_builder(), db=db))
    line = cart["items"][0]

    assert line["supplier_name"] == "Verified Supplier"
    assert line["vendor_id"] is None
    assert line["vendor_open_to_negotiate"] is False


def test_vendor_flag_coexists_with_the_agreed_negotiated_price():
    """An agreed price and the opt-in flag travel together on the same line.

    The price is attached to the cart LINE (via `negotiation_id`), never to the
    product: two lines of the same product can carry different deals.
    """
    negotiation_id = uuid4()
    item = _cart_item(PRODUCT_A, negotiation_id=negotiation_id)
    agreed = SimpleNamespace(
        id=negotiation_id, product_id=PRODUCT_A, agreed_price=7600.0, quantity=10,
    )
    db = _FakeSession([
        [item],
        [agreed],
        [(PRODUCT_A, VENDOR_ID, "Dangote Cement Plc", True)],
        _product(PRODUCT_A),
        _image(),
    ])

    cart = asyncio.run(get_cart(current_user=_builder(), db=db))
    line = cart["items"][0]

    assert line["negotiation_price"] == 7600.0
    assert line["negotiation_id"] == str(negotiation_id)
    assert line["negotiated_quantity"] == 10
    assert line["vendor_open_to_negotiate"] is True
    assert line["supplier_name"] == "Dangote Cement Plc"


def test_unseeded_cart_line_has_no_negotiated_price():
    """No `negotiation_id` means no agreed price, whatever the buyer once agreed."""
    item = _cart_item(PRODUCT_A)
    db = _FakeSession([
        [item],
        [(PRODUCT_A, VENDOR_ID, "Dangote Cement Plc", True)],
        _product(PRODUCT_A),
        _image(),
    ])

    cart = asyncio.run(get_cart(current_user=_builder(), db=db))
    line = cart["items"][0]

    assert line["negotiation_price"] is None
    assert line["negotiation_id"] is None
