"""Demand broadcasts end by agreement, not by a 48h clock.

Contract pinned here:
  * The legacy 48h cutoff is frozen at the release timestamp: alerts raised before
    it keep their historical expiry, alerts raised after it can never expire.
  * `mark_demand_agreed` stamps the alert + winning thread and rejects every
    sibling thread, so a demand can never be agreed in two places.
  * A guest (no `requested_by`) never creates a demand alert.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models.demand_alert import (
    DEMAND_ALERT_TTL_FROZEN_AT,
    DEMAND_ALERT_TTL_HOURS,
    demand_alert_cutoff,
)
from app.services import demand_broadcast_service as svc

BUILDER_ID = uuid4()
VENDOR_ID = uuid4()


class _FakeResult:
    def __init__(self, values):
        self._values = values if isinstance(values, list) else [values]

    def scalars(self):
        return self

    def first(self):
        return self._values[0] if self._values else None

    def all(self):
        return list(self._values)


class _FakeSession:
    """Scripted async session: each execute() pops the next scripted result."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.added = []
        self.calls = 0
        self.flushed = False

    async def execute(self, stmt):
        self.calls += 1
        if not self._results:
            raise AssertionError("unexpected extra db.execute() call")
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True


def test_cutoff_stops_advancing_after_the_freeze():
    frozen_cutoff = DEMAND_ALERT_TTL_FROZEN_AT - timedelta(hours=DEMAND_ALERT_TTL_HOURS)

    assert demand_alert_cutoff(DEMAND_ALERT_TTL_FROZEN_AT + timedelta(days=365)) == frozen_cutoff
    # Before the freeze the rolling window still applies.
    before = DEMAND_ALERT_TTL_FROZEN_AT - timedelta(hours=1)
    assert demand_alert_cutoff(before) == before - timedelta(hours=DEMAND_ALERT_TTL_HOURS)


def test_cutoff_is_timezone_aware():
    assert demand_alert_cutoff().tzinfo is not None


def test_mark_demand_agreed_stamps_the_alert_and_rejects_siblings():
    alert = SimpleNamespace(
        id=uuid4(), status="pending", agreed_vendor_id=None, agreed_price=None,
        agreed_at=None, agreed_by=None,
    )
    winner = SimpleNamespace(
        id=uuid4(), supplier_id=VENDOR_ID, status="counter_offered",
        agreed_price=None, agreed_at=None, agreed_by=None, bid_price=None,
    )
    loser = SimpleNamespace(id=uuid4(), status="counter_offered")
    db = _FakeSession([[loser]])

    siblings = asyncio.run(
        svc.mark_demand_agreed(db, alert, winner, price=4200, accepted_by="builder")
    )

    assert alert.status == "agreed"
    assert alert.agreed_vendor_id == VENDOR_ID
    assert float(alert.agreed_price) == 4200
    assert alert.agreed_by == "builder"
    assert isinstance(alert.agreed_at, datetime)
    assert winner.status == "price_agreed"
    assert float(winner.agreed_price) == 4200
    assert float(winner.bid_price) == 4200
    assert [s.id for s in siblings] == [loser.id]
    assert loser.status == "rejected"
    assert db.flushed is True


def test_guest_broadcasts_never_create_alerts():
    """`requested_by=None` means no owner to negotiate with: nothing is written."""
    db = _FakeSession([])

    summary = asyncio.run(
        svc.broadcast_boq_demand(
            db,
            [{"description": "Cement 50kg", "quantity": 10, "unit": "bag"}],
            project_title="Duplex",
            city="Abuja",
            requested_by=None,
        )
    )

    assert summary["alerts_created"] == 0
    assert summary["bids_opened"] == 0
    assert db.calls == 0
    assert db.added == []


def test_broadcast_without_a_session_is_a_no_op():
    summary = asyncio.run(
        svc.broadcast_boq_demand(None, [{"description": "Cement", "quantity": 1}])
    )
    assert summary == {
        "alerts_created": 0, "alerts_updated": 0, "bids_opened": 0, "notified": 0,
    }
