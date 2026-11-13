"""Order-message notifications deep-link into the chat panel's Messages tab.

Contract pinned here:
  * A new order message still notifies with type "order" (the bell's grouping and
    filter are unchanged) but links to "/chat/messages/<order_id>".
  * Both roles get the same link: buyers read the threads in the dashboard chat
    panel and vendors in the supplier assistant, so one target serves both.
  * The old list-only links ("/dashboard/orders" / "/supplier-dashboard?tab=orders")
    are gone - `routeNotification` turns the new link into the shared
    "burncost:chat-open" event instead of navigating to an orders list.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.endpoints.messaging import MessageCreate, send_order_message
from app.models.notification import Notification

BUYER_ID = uuid4()
VENDOR_USER_ID = uuid4()
VENDOR_ID = uuid4()
ORDER_ID = uuid4()


# ── test doubles ─────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, values):
        self._values = values if isinstance(values, list) else [values]

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)

    def first(self):
        return self._values[0] if self._values else None


class _FakeSession:
    """Scripted async session: execute() pops the next scripted result.

    Call order is fixed: `_load_order`, then (buyer senders only) the batched
    vendor-user lookup that resolves the recipients.
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

    async def refresh(self, obj):
        return None


def _order():
    return SimpleNamespace(
        id=ORDER_ID,
        user_id=BUYER_ID,
        order_number="ORD-1042",
        items=[SimpleNamespace(vendor_id=VENDOR_ID)],
    )


def _notifications(db):
    return [o for o in db.added if isinstance(o, Notification)]


# ── tests ────────────────────────────────────────────────────────────────────

def test_buyer_message_notifies_vendor_into_chat_threads():
    """A buyer's message notifies each fulfilling vendor with a chat deep link."""
    db = _FakeSession([_order(), [VENDOR_USER_ID]])

    asyncio.run(send_order_message(
        order_id=ORDER_ID,
        payload=MessageCreate(body="Can you deliver to site on Friday?"),
        current_user=SimpleNamespace(id=BUYER_ID),
        db=db,
    ))

    notes = _notifications(db)
    assert len(notes) == 1
    assert notes[0].type == "order"
    assert notes[0].title == "New message on your order"
    assert notes[0].link == f"/chat/messages/{ORDER_ID}"
    assert notes[0].user_id == VENDOR_USER_ID
    # items + vendor lookup, no extra round trips.
    assert db.executed == 2


def test_vendor_message_notifies_buyer_with_the_same_chat_link():
    """Vendor replies land on the buyer's dashboard chat panel, same target."""
    vendor = SimpleNamespace(id=VENDOR_ID, user_id=VENDOR_USER_ID)
    db = _FakeSession([_order(), vendor])

    asyncio.run(send_order_message(
        order_id=ORDER_ID,
        payload=MessageCreate(body="Friday morning works - driver will call."),
        current_user=SimpleNamespace(id=VENDOR_USER_ID),
        db=db,
    ))

    notes = _notifications(db)
    assert len(notes) == 1
    assert notes[0].user_id == BUYER_ID
    assert notes[0].link == f"/chat/messages/{ORDER_ID}"
    assert "/dashboard/orders" not in str(notes[0].link)
