"""Promo notifications deep-link each recipient into THEIR OWN dashboard.

Contract pinned here:
  * A promo broadcast reaches customers AND vendors (platform promos), but the
    in-app link is per recipient: vendors get
    "/supplier-dashboard?tab=promotions" (the tab that redeems codes) and
    everyone else keeps "/dashboard".
  * Regression guard: the link used to be computed once for the whole audience
    as "/dashboard", which dropped vendors on the builder dashboard.
  * The per-recipient pref gate is untouched: an opted-out user gets no row even
    when their role has a valid link.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.models.notification import Notification
from app.models.user import UserRole
from app.services.promo_notification_service import _recipient_link, notify_promo_created

CUSTOMER_ID = uuid4()
VENDOR_USER_ID = uuid4()
OPTED_OUT_ID = uuid4()


# ── test doubles ─────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, values):
        self._values = values if isinstance(values, list) else [values]

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _FakeSession:
    """Scripted async session: execute() pops the next scripted result.

    Call order is fixed by `notify_promo_created`: the audience rows first, then
    the marketing opt-in email list.
    """

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.executed = 0

    async def execute(self, stmt):
        self.executed += 1
        if not self._results:
            raise AssertionError("unexpected extra db.execute() call")
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _promo():
    return SimpleNamespace(
        code="SAVE10",
        discount_amount=5000,
        min_order_amount=0,
        expires_at=None,
        description="Building materials week",
    )


def _notifications(db):
    return [o for o in db.added if isinstance(o, Notification)]


# ── tests ────────────────────────────────────────────────────────────────────

def test_mixed_audience_gets_role_appropriate_promo_link():
    """One broadcast: the vendor row points at the supplier tab, the buyer at /dashboard."""
    rows = [
        (CUSTOMER_ID, "buyer@example.com", UserRole.CUSTOMER, {}),
        (VENDOR_USER_ID, "vendor@example.com", UserRole.VENDOR, {}),
        (OPTED_OUT_ID, "muted@example.com", UserRole.CUSTOMER, {"push_promotions": False}),
    ]
    db = _FakeSession([rows, []])

    summary = asyncio.run(notify_promo_created(
        db, _promo(), vendor_id=None, scope_label="on building materials",
    ))

    notes = {n.user_id: n for n in _notifications(db)}
    assert set(notes) == {CUSTOMER_ID, VENDOR_USER_ID}
    assert notes[VENDOR_USER_ID].link == "/supplier-dashboard?tab=promotions"
    assert notes[CUSTOMER_ID].link == "/dashboard"
    assert summary["in_app"] == 2
    # Vendor rows must never carry a builder route.
    assert "/dashboard" != notes[VENDOR_USER_ID].link
    # audience lookup + opt-in emails, no extra round trips.
    assert db.executed == 2


def test_opted_out_user_gets_no_row_regardless_of_role():
    """The pref gate still wins: the muted vendor is skipped, the active one is not."""
    rows = [
        (VENDOR_USER_ID, "vendor@example.com", UserRole.VENDOR, {}),
        (OPTED_OUT_ID, "muted-vendor@example.com", UserRole.VENDOR, {"promotions": False}),
    ]
    db = _FakeSession([rows, []])

    asyncio.run(notify_promo_created(db, _promo(), vendor_id=None))

    notes = _notifications(db)
    assert [n.user_id for n in notes] == [VENDOR_USER_ID]
    assert notes[0].link == "/supplier-dashboard?tab=promotions"


def test_recipient_link_handles_enum_and_string_roles():
    """`role` arrives as a UserRole member or a raw string depending on the driver."""
    assert _recipient_link(UserRole.VENDOR) == "/supplier-dashboard?tab=promotions"
    assert _recipient_link("vendor") == "/supplier-dashboard?tab=promotions"
    assert _recipient_link("VENDOR") == "/supplier-dashboard?tab=promotions"
    assert _recipient_link(UserRole.CUSTOMER) == "/dashboard"
    assert _recipient_link(None) == "/dashboard"
