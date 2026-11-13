"""
Two-party agreed final price for negotiations + its checkout auto-apply.

Contract pinned here:
  * Only the counterparty of the last proposer may accept a proposed price; a
    proposer cannot accept their own price, and non-parties get a 403.
  * Once `price_agreed`, both parties are locked out of further counters (400).
  * `negotiated_cart_lines` only prices the cart line seeded from a builder's own,
    unused agreement - an agreed price is per-transaction, not per-product.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.negotiations import (
    create_negotiation,
    send_counter_offer,
    _final_discount_pct,
    _neg_dict,
)
from app.api.v1.endpoints.admin_negotiation import (
    _final_discount_pct as _admin_final_discount_pct,
    _neg_dict as _admin_neg_dict,
)
from app.api.v1.endpoints.orders import negotiated_cart_lines
from app.models.negotiation import NegotiationAuditEntry
from app.models.notification import Notification

BUILDER_ID = uuid4()
SUPPLIER_ID = uuid4()
USER_ID = uuid4()


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

    def first(self):
        return self._values[0] if self._values else None

    def all(self):
        return list(self._values)


class _FakeSession:
    """Scripted async session: each execute() pops the next scripted result."""

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


def _neg(counter_offers=None, **overrides):
    base = dict(
        id=uuid4(),
        negotiation_number="NEG-TEST-1",
        builder_id=BUILDER_ID,
        supplier_id=SUPPLIER_ID,
        product_id=None,
        supplier=SimpleNamespace(business_name="Acme Supplies"),
        product_name="Cement 50kg",
        category="Cement",
        quantity=10,
        unit="bag",
        requested_discount=5,
        requested_price=None,
        counter_offer=None,
        final_discount=None,
        value=0,
        status="pending",
        created_at=None,
        agreed_price=None,
        agreed_at=None,
        agreed_by=None,
        agreed_order_id=None,
        demand_alert_id=None,
        counter_offers=counter_offers or [],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _builder():
    return SimpleNamespace(id=BUILDER_ID, role="customer")


def _supplier_user():
    return SimpleNamespace(id=USER_ID, role="vendor")


# ── create / propose / accept ────────────────────────────────────────────────

def test_create_negotiation_notifies_the_supplier():
    """A builder opening a request must leave a persistent row for the vendor."""
    supplier = SimpleNamespace(id=SUPPLIER_ID, user_id=USER_ID, business_name="Acme Supplies")
    row = _neg()
    db = _FakeSession([supplier, 1, row])  # vendor, negotiation count, re-fetch
    builder = SimpleNamespace(id=BUILDER_ID, role="customer", email="builder@example.com")

    asyncio.run(
        create_negotiation(
            payload={
                "supplier_id": str(SUPPLIER_ID),
                "product_name": "Cement 50kg",
                "requested_discount": 7.5,
                "quantity": 10,
                "unit": "bag",
            },
            current_user=builder,
            db=db,
        )
    )

    notes = [x for x in db.added if isinstance(x, Notification)]
    assert len(notes) == 1
    assert notes[0].user_id == USER_ID
    assert notes[0].type == "negotiation"
    assert notes[0].link == "/supplier-dashboard"
    assert notes[0].read is False
    assert "builder@example.com" in notes[0].message
    assert "7.5%" in notes[0].message


def test_propose_price_records_offer_and_sets_status():
    n = _neg()
    db = _FakeSession([n, [], n])  # negotiation, vendor(None=builder), re-fetch

    result = asyncio.run(
        send_counter_offer(
            negotiation_id=n.id,
            payload={"action": "propose_price", "final_price": 4200, "note": "best"},
            current_user=_builder(),
            db=db,
        )
    )

    assert n.status == "price_proposed"
    assert n.agreed_price is None
    offer = db.added[0]
    assert float(offer.final_price) == 4200
    assert offer.offered_by == "builder"
    assert result["status"] == "price_proposed"


def test_propose_price_requires_positive_final_price():
    n = _neg()
    db = _FakeSession([n, []])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            send_counter_offer(
                negotiation_id=n.id,
                payload={"action": "propose_price"},
                current_user=_builder(),
                db=db,
            )
        )
    assert caught.value.status_code == 400


def _offer(offered_by="builder", final_price=4200):
    return SimpleNamespace(
        id=uuid4(), offered_by=offered_by, discount_percent=5,
        final_price=final_price, note="",
        product_id=None, product_snapshot=None, created_at=None,
    )


def test_counterparty_can_accept_the_proposed_price():
    offer = _offer()
    n = _neg(counter_offers=[offer])
    staff_a, staff_b = uuid4(), uuid4()
    # negotiation, vendor(=supplier), latest offer, re-fetch, then the admin
    # notification lookups: supplier business name, buyer email, console staff ids
    db = _FakeSession([
        n,
        [SimpleNamespace(id=SUPPLIER_ID, user_id=USER_ID)],
        offer,
        n,
        ["Acme Supplies"],
        ["builder@example.com"],
        [staff_a, staff_b],
    ])

    result = asyncio.run(
        send_counter_offer(
            negotiation_id=n.id,
            payload={"action": "accept_price"},
            current_user=_supplier_user(),
            db=db,
        )
    )

    assert n.status == "price_agreed"
    assert float(n.agreed_price) == 4200
    assert n.agreed_by == "supplier"
    assert result["agreed_price"] == 4200
    assert result["agreed_by"] == "supplier"

    # The private agreement is recorded and every console user is notified.
    audit = [a for a in db.added if isinstance(a, NegotiationAuditEntry)]
    assert len(audit) == 1
    assert audit[0].action.startswith("Final price agreed")
    assert audit[0].performed_by == "supplier"
    assert audit[0].new_value == str(n.agreed_price)

    notes = [x for x in db.added if isinstance(x, Notification)]
    staff_notes = [x for x in notes if str(x.user_id) in {str(staff_a), str(staff_b)}]
    assert {str(x.user_id) for x in staff_notes} == {str(staff_a), str(staff_b)}
    assert all(x.type == "negotiation" and x.link == "/negotiation" for x in staff_notes)
    assert all(n.negotiation_number in (x.message or "") for x in staff_notes)

    # The buyer who did not accept also gets a persistent row (self-service loop).
    buyer_notes = [x for x in notes if str(x.user_id) == str(BUILDER_ID)]
    assert len(buyer_notes) == 1
    assert buyer_notes[0].type == "negotiation"
    assert buyer_notes[0].link == "/dashboard/negotiations"
    assert "agreed" in buyer_notes[0].title.lower()


def test_proposer_cannot_accept_their_own_price():
    offer = _offer()
    n = _neg(counter_offers=[offer])
    db = _FakeSession([n, [], offer])

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            send_counter_offer(
                negotiation_id=n.id,
                payload={"action": "accept_price"},
                current_user=_builder(),
                db=db,
            )
        )
    assert caught.value.status_code == 400
    assert "own price" in str(caught.value.detail).lower()


def test_non_party_is_rejected():
    n = _neg()
    db = _FakeSession([n, []])  # no vendor, id != builder -> outsider
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            send_counter_offer(
                negotiation_id=n.id,
                payload={"action": "propose_price", "final_price": 100},
                current_user=SimpleNamespace(id=uuid4(), role="customer"),
                db=db,
            )
        )
    assert caught.value.status_code == 403


def test_agreed_price_is_locked_out_from_further_counters():
    n = _neg(status="price_agreed", agreed_price=4200)
    db = _FakeSession([n, []])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            send_counter_offer(
                negotiation_id=n.id,
                payload={"action": "counter", "discount_percent": 8},
                current_user=_builder(),
                db=db,
            )
        )
    assert caught.value.status_code == 400
    assert "already agreed" in str(caught.value.detail).lower()


# ── per-transaction agreed prices (cart-line scoped) ─────────────────────────

def test_negotiated_cart_lines_only_return_this_builders_deals():
    pid_a = uuid4()
    row = _neg(product_id=pid_a, agreed_price=4200)
    cart = [SimpleNamespace(negotiation_id=row.id)]
    db = _FakeSession([[row]])

    agreed = asyncio.run(negotiated_cart_lines(db, BUILDER_ID, cart))

    assert agreed == {str(row.id): row}


def test_negotiated_cart_lines_empty_when_no_lines_were_seeded():
    db = _FakeSession([])
    assert asyncio.run(
        negotiated_cart_lines(db, BUILDER_ID, [SimpleNamespace(negotiation_id=None)])
    ) == {}



# ── counterparty notifications (both sides see the loop progress) ────────────

def test_builder_propose_price_notifies_the_vendor():
    """A builder's proposal must reach the vendor's notifications inbox."""
    supplier = SimpleNamespace(business_name="Acme Supplies", user_id=USER_ID)
    n = _neg(supplier=supplier)
    db = _FakeSession([n, [], n])  # negotiation, vendor(None=builder), re-fetch

    asyncio.run(
        send_counter_offer(
            negotiation_id=n.id,
            payload={"action": "propose_price", "final_price": 4200},
            current_user=_builder(),
            db=db,
        )
    )

    notes = [x for x in db.added if isinstance(x, Notification)]
    assert len(notes) == 1
    assert str(notes[0].user_id) == str(USER_ID)
    assert notes[0].type == "negotiation"
    assert notes[0].link == "/supplier-dashboard"
    assert notes[0].read is False
    assert "4,200.00" in notes[0].message
    assert n.negotiation_number in notes[0].message


def test_vendor_counter_notifies_the_builder():
    """A vendor counter-offer must reach the buyer's notifications inbox."""
    n = _neg()
    db = _FakeSession([
        n,
        [SimpleNamespace(id=SUPPLIER_ID, user_id=USER_ID)],
        n,
    ])

    asyncio.run(
        send_counter_offer(
            negotiation_id=n.id,
            payload={"action": "counter", "discount_percent": 8},
            current_user=_supplier_user(),
            db=db,
        )
    )

    notes = [x for x in db.added if isinstance(x, Notification)]
    assert len(notes) == 1
    assert str(notes[0].user_id) == str(BUILDER_ID)
    assert notes[0].type == "negotiation"
    assert notes[0].link == "/dashboard/negotiations"
    assert "8%" in notes[0].message


def test_counterparty_notification_never_breaks_a_completed_action():
    """A missing counterparty (deleted vendor account) is skipped, not a 500."""
    n = _neg(supplier=SimpleNamespace(business_name="Acme Supplies"))
    db = _FakeSession([n, [], n])

    result = asyncio.run(
        send_counter_offer(
            negotiation_id=n.id,
            payload={"action": "decline"},
            current_user=_builder(),
            db=db,
        )
    )

    assert result["status"] == "rejected"
    assert [x for x in db.added if isinstance(x, Notification)] == []


# ── the standing counter shown on the row (percentage column semantics) ──────

def test_propose_price_stores_the_percentage_the_naira_implies():
    """`Counter: ₦4,200 (70% off)` - the stored % must describe the proposal.

    The row renders the counter as `₦<final_price> (<discount_percent>% off)`, so a
    proposal that carried no explicit percentage must derive it from the product's
    catalogue price (same maths as a buyer's bid) instead of echoing the buyer's ask.
    """
    pid = uuid4()
    n = _neg(product_id=pid, requested_discount=5)
    # negotiation, vendor lookup (builder side), product list price, re-fetch
    db = _FakeSession([n, [], 14000, n])

    asyncio.run(
        send_counter_offer(
            negotiation_id=n.id,
            payload={"action": "propose_price", "final_price": 4200},
            current_user=_builder(),
            db=db,
        )
    )

    offer = db.added[0]
    assert float(offer.final_price) == 4200
    assert float(offer.discount_percent) == 70.0


def test_accept_price_stores_the_agreed_percentage_not_the_naira():
    """`counter_offer` is Numeric(5,2) = a percentage: an agreed ₦11,000 overflows
    it and, before, the row rendered the naira figure as "11000%". The agreed
    amount belongs in `agreed_price` only."""
    offer = _offer(final_price=11000)
    offer.discount_percent = 8.33
    n = _neg(counter_offers=[offer])
    # negotiation, vendor(=supplier), latest offer, re-fetch, then the admin
    # notification lookups: supplier business name, buyer email, console staff ids
    db = _FakeSession([
        n,
        [SimpleNamespace(id=SUPPLIER_ID, user_id=USER_ID)],
        offer,
        n,
        ["Acme Supplies"],
        ["builder@example.com"],
        [],
    ])

    result = asyncio.run(
        send_counter_offer(
            negotiation_id=n.id,
            payload={"action": "accept_price"},
            current_user=_supplier_user(),
            db=db,
        )
    )

    assert float(n.agreed_price) == 11000
    assert float(n.counter_offer) == 8.33
    assert result["counter_offer"] == 8.33
    assert result["agreed_price"] == 11000
    # The list/modal read `final_discount`: it must report the agreed %, not NULL.
    assert result["final_discount"] == 8.33


def test_a_stale_stored_percentage_never_reaches_the_row():
    """Reported: `Requested: ₦11,000 (8.33% off)` + `Counter: ₦11,200 (8.33% off)`.

    Both offers were written while 8.33% was the going rate, so the stored column
    cannot be trusted beside a naira figure: ₦11,400 is 5% off a ₦12,000 list
    price and ₦11,200 is 6.67%. Each priced offer is re-derived from the list
    price when it is rendered, so the two figures can never disagree.
    """
    pid = uuid4()
    vendor_offer = _offer(offered_by="supplier", final_price=11400)
    vendor_offer.discount_percent = 8.33
    builder_offer = _offer(offered_by="builder", final_price=11200)
    builder_offer.discount_percent = 8.33
    n = _neg(product_id=pid, counter_offers=[vendor_offer, builder_offer])
    # negotiation, vendor(=supplier), list price, re-fetch
    db = _FakeSession([
        n,
        [SimpleNamespace(id=SUPPLIER_ID, user_id=USER_ID)],
        12000,
        n,
    ])

    result = asyncio.run(
        send_counter_offer(
            negotiation_id=n.id,
            payload={"action": "propose_price", "final_price": 11200},
            current_user=_supplier_user(),
            db=db,
        )
    )

    offers = result["counter_offers"]
    assert offers[0]["discount_percent"] == 5.0    # ₦11,400 off ₦12,000
    assert offers[1]["discount_percent"] == 6.67   # ₦11,200 off ₦12,000
    assert offers[1]["final_price"] == 11200


def test_price_agreed_row_reports_the_agreed_percentage():
    """Reported: an agreed deal showed `Final Discount: Pending` in the console
    and `Final discount: -` in the builder list. `accept_price` only writes
    `agreed_price`/`counter_offer`, so `final_discount` stayed NULL and both
    serializers read that column straight. It is now derived for the row.
    """
    n = _neg(status="price_agreed", agreed_price=11200, counter_offer=None)

    row = _neg_dict(n, base_price=12000)
    assert row["final_discount"] == 6.67          # ₦11,200 off ₦12,000
    assert row["agreed_price"] == 11200

    # No catalogue price to recompute from -> the stored implied % (written by
    # `accept_price`) is the fallback.
    n.counter_offer = 6.67
    assert _admin_final_discount_pct(n) == 6.67

    # A stored `final_discount` (admin approve / legacy accept) keeps winning.
    assert _final_discount_pct(_neg(status="approved", final_discount=5.0,
                                    counter_offer=9.9)) == 5.0
    assert _admin_final_discount_pct(_neg(status="approved", final_discount=5.0,
                                          counter_offer=9.9)) == 5.0


def test_admin_negotiation_dict_exposes_the_agreed_price_and_when():
    """The console modal reads `agreed_price`/`agreed_at`/`agreed_by` to show how
    and when the deal was struck; `_final_discount_pct` never returns NULL once
    it is agreed."""
    agreed_at = datetime(2026, 10, 11, 9, 30)
    n = _neg(status="price_agreed", agreed_price=11200, agreed_at=agreed_at,
             agreed_by="supplier", counter_offer=6.67,
             builder=SimpleNamespace(email="b@example.com", profile=None),
             flagged=False, suspended=False)

    row = _admin_neg_dict(n)
    assert row["agreed_price"] == 11200
    assert row["agreed_at"] == agreed_at.isoformat()
    assert row["agreed_by"] == "supplier"
    assert row["final_discount"] == 6.67


