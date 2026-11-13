"""Checkout idempotency: one cart must never yield two order ids.

Regression pins:
  * A repeat checkout for the same basket + promo reuses the unpaid draft
    instead of inserting a twin (the "confirmed + pending payment" pair).
  * The basket fingerprint ignores line order but keeps quantities.
  * Paying one order cancels any unpaid twin for the same basket, so the
    dashboard cannot keep showing a phantom pending order.
  * The one-off reconciliation script only pairs drafts with a paid order from
    the same checkout burst.
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.endpoints.orders import _cart_fingerprint, _find_recent_draft, _find_reusable_draft
from app.api.v1.endpoints.payments import _cancel_duplicate_drafts
from app.models.order import OrderStatus
from app.scripts.cancel_duplicate_drafts import _find_duplicates

BUILDER_ID = uuid4()
PRODUCT_A = uuid4()
PRODUCT_B = uuid4()


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.execute_calls = 0

    async def execute(self, _query):
        self.execute_calls += 1
        return _FakeResult(self.rows)


def _order(*items, status="pending_payment", payment_status="pending", promo_code=None,
           created_at=None, user_id=BUILDER_ID):
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        order_number=f"BC-TEST-{uuid4().hex[:6].upper()}",
        items=[SimpleNamespace(product_id=p, quantity=q) for p, q in items],
        status=status,
        payment_status=payment_status,
        promo_code=promo_code,
        created_at=created_at or datetime.utcnow(),
    )


def test_fingerprint_is_order_insensitive_but_quantity_sensitive():
    cart = [
        {"product_id": PRODUCT_A, "quantity": 2},
        {"product_id": PRODUCT_B, "quantity": 1},
    ]
    stored = [
        SimpleNamespace(product_id=PRODUCT_B, quantity=1),
        SimpleNamespace(product_id=PRODUCT_A, quantity=2),
    ]
    assert _cart_fingerprint(cart) == _cart_fingerprint(stored)

    changed = [
        {"product_id": PRODUCT_A, "quantity": 3},
        {"product_id": PRODUCT_B, "quantity": 1},
    ]
    assert _cart_fingerprint(cart) != _cart_fingerprint(changed)


def test_reuses_matching_unpaid_draft():
    draft = _order((PRODUCT_A, 2), (PRODUCT_B, 1), promo_code="SAVE10")
    db = _FakeSession([draft])
    fingerprint = _cart_fingerprint([
        {"product_id": PRODUCT_B, "quantity": 1},
        {"product_id": PRODUCT_A, "quantity": 2},
    ])

    found = asyncio.run(_find_reusable_draft(db, BUILDER_ID, fingerprint, promo_code="save10"))

    assert found is draft


def test_does_not_reuse_when_promo_differs():
    draft = _order((PRODUCT_A, 2), promo_code="SAVE10")
    db = _FakeSession([draft])
    fingerprint = _cart_fingerprint([{"product_id": PRODUCT_A, "quantity": 2}])

    assert asyncio.run(_find_reusable_draft(db, BUILDER_ID, fingerprint, promo_code="SAVE20")) is None


def test_does_not_reuse_when_items_or_state_differ():
    fingerprint = _cart_fingerprint([{"product_id": PRODUCT_A, "quantity": 2}])
    cases = [
        _order((PRODUCT_A, 3)),                                    # different quantity
        _order((PRODUCT_A, 2), payment_status="completed"),        # already paid
        _order((PRODUCT_A, 2), status=OrderStatus.CONFIRMED),      # no longer a draft
    ]
    for draft in cases:
        db = _FakeSession([draft])
        assert asyncio.run(_find_reusable_draft(db, BUILDER_ID, fingerprint)) is None


def test_paid_order_cancels_only_the_matching_unpaid_twin():
    paid = _order(
        (PRODUCT_A, 2), status=OrderStatus.CONFIRMED,
        payment_status="completed", user_id=BUILDER_ID,
    )
    twin = _order((PRODUCT_A, 2), user_id=BUILDER_ID)
    unrelated = _order((PRODUCT_B, 5), user_id=BUILDER_ID)
    db = _FakeSession([paid, twin, unrelated])

    cancelled = asyncio.run(_cancel_duplicate_drafts(db, paid, paid.items))

    assert cancelled == 1
    assert twin.status == OrderStatus.CANCELLED
    assert paid.order_number in twin.admin_notes
    assert unrelated.status != OrderStatus.CANCELLED


def test_find_recent_draft_returns_the_newest_candidate():
    newer = _order((PRODUCT_A, 1))
    older = _order((PRODUCT_B, 1))
    db = _FakeSession([newer, older])

    found = asyncio.run(_find_recent_draft(db, BUILDER_ID, timedelta(minutes=2)))

    assert found is newer


def test_reconciliation_only_pairs_same_burst():
    now = datetime.utcnow()
    paid = _order(
        (PRODUCT_A, 2), status=OrderStatus.CONFIRMED,
        payment_status="completed", created_at=now,
    )
    twin = _order((PRODUCT_A, 2), created_at=now + timedelta(seconds=20))
    later_reorder = _order((PRODUCT_A, 2), created_at=now + timedelta(days=3))

    matches = _find_duplicates([paid, twin, later_reorder], timedelta(minutes=10))

    assert [(draft.order_number, target.order_number) for draft, target in matches] == [
        (twin.order_number, paid.order_number)
    ]
