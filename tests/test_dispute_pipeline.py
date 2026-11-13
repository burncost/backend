"""Dispute-resolution spine: escrow hold, inspection clock, refund records.

No database and no network: the guard/plan/refund services are driven with
scripted fakes, matching `test_admin_escrow_breakdown.py`.

Covers the policy-critical regressions:

  * escrow is NOT queued while a dispute is open (the old release path paid the
    vendor regardless of disputes);
  * the inspection window is stamped on delivery and can only be extended once
    by the buyer;
  * a refund moves the order's payment status and blocks settlement accrual.
"""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.services import escrow_service, refund_service


# ── fakes ───────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    """Scripted `execute` results, plus the write operations the services use."""

    def __init__(self, results=()):
        self._results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return _Result(self._results.pop(0) if self._results else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1


class _BackgroundTasks:
    def __init__(self):
        self.calls = []

    def add_task(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))


def _order(**overrides):
    base = dict(
        id=uuid4(),
        order_number="ORD-1",
        status="delivered",
        payment_status="completed",
        total_amount=Decimal("1000.00"),
        delivered_at=datetime(2026, 10, 20, 12, 0, 0),
        inspection_window_hours=48,
        auto_release_at=None,
        escrow_released_at=None,
        inspection_extension_requested_at=None,
        items=[SimpleNamespace(vendor_id="vendor-1", total_price=Decimal("1000.00"))],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── inspection clock ────────────────────────────────────────────────────────

def test_window_is_stamped_from_verified_delivery():
    order = _order()
    deadline = escrow_service.stamp_inspection_window(order)

    assert deadline == datetime(2026, 10, 20, 12, 0, 0) + timedelta(hours=48)
    assert order.auto_release_at == deadline
    assert order.inspection_window_hours == 48


def test_window_stamp_is_idempotent():
    order = _order()
    first = escrow_service.stamp_inspection_window(order)
    # A second confirmation must not shorten the buyer's inspection window.
    second = escrow_service.stamp_inspection_window(order, now=datetime(2026, 10, 22))

    assert first == second


def test_window_falls_back_to_config_when_column_is_null():
    order = _order(inspection_window_hours=None)
    assert escrow_service.inspection_window_hours(order) == 48


def test_extension_defaults_to_a_single_day():
    # Policy: one buyer-requested extension, 24 hours.
    assert escrow_service.extension_hours() == 24


def test_inline_release_is_withheld_while_a_dispute_is_open():
    db = _FakeDB(results=[[SimpleNamespace(id="dispute-1")]])
    order = _order()

    result = asyncio.run(escrow_service.release_order_escrow_now(db, order))

    assert result["released"] is False
    assert result["reason"] == "dispute_hold"
    assert order.escrow_released_at is None


# ── release guard ───────────────────────────────────────────────────────────

def test_open_dispute_blocks_the_queued_release():
    db = _FakeDB(results=[[SimpleNamespace(id="dispute-1")]])
    order = _order()
    tasks = _BackgroundTasks()

    result = asyncio.run(escrow_service.release_order_escrow(tasks, order, db))

    assert result == {"released": False, "reason": "dispute_hold", "order_number": "ORD-1"}
    assert tasks.calls == []
    assert order.escrow_released_at is None


def test_release_is_idempotent_once_released():
    order = _order(escrow_released_at=datetime(2026, 10, 22, 12, 0, 0))
    tasks = _BackgroundTasks()

    result = asyncio.run(escrow_service.release_order_escrow(tasks, order, None))

    assert result["reason"] == "already_released"
    assert tasks.calls == []


def test_partially_refunded_order_is_not_paid_out_again():
    order = _order(payment_status="partially_refunded")

    reason = asyncio.run(escrow_service.release_block_reason(None, order))

    assert reason == "order_refunded"


def test_cancelled_order_is_not_releasable():
    order = _order(status="cancelled")

    reason = asyncio.run(escrow_service.release_block_reason(None, order))

    assert reason == "order_not_releasable"


def test_release_is_queued_when_no_dispute_and_not_released(monkeypatch):
    async def _margins(_db, _items):
        return {}

    monkeypatch.setattr(
        "app.services.pricing_service.vendor_margins_by_item", _margins
    )
    db = _FakeDB(results=[[]])  # no open dispute
    order = _order()
    tasks = _BackgroundTasks()

    result = asyncio.run(escrow_service.release_order_escrow(tasks, order, db))

    assert result["released"] is True
    assert len(tasks.calls) == 1
    assert tasks.calls[0][2]["amount"] == 1000.0
    assert order.escrow_released_at is not None


# ── partial release math ────────────────────────────────────────────────────

def test_partial_release_scales_each_line():
    order = _order(items=[
        SimpleNamespace(vendor_id="vendor-1", total_price=Decimal("1000.00")),
        SimpleNamespace(vendor_id="vendor-2", total_price=Decimal("500.00")),
    ])

    plan = escrow_service.release_plan(order, {}, scale=0.5)

    assert [p["amount"] for p in plan] == [500.0, 250.0]
    assert [p["gross_amount"] for p in plan] == [500.0, 250.0]


def test_zero_scale_releases_nothing():
    order = _order()
    assert escrow_service.release_plan(order, {}, scale=0.0) == []


# ── refunds ─────────────────────────────────────────────────────────────────

def test_partial_refund_marks_the_order_partially_refunded():
    order = _order()
    db = _FakeDB(results=[[Decimal("400.00")]])

    row = asyncio.run(refund_service.record_refund(
        db, order=order, amount=400, reason="dispute", requested_by="admin",
    ))

    assert row is not None
    assert db.added == [row]
    assert order.payment_status == "partially_refunded"
    assert order.status == "delivered"


def test_full_refund_flips_the_order_to_refunded():
    order = _order()
    db = _FakeDB(results=[[Decimal("1000.00")]])

    asyncio.run(refund_service.record_refund(db, order=order, amount=1000))

    assert order.payment_status == "refunded"
    assert order.status == "refunded"


def test_refund_of_zero_is_ignored():
    order = _order()
    db = _FakeDB()

    row = asyncio.run(refund_service.record_refund(db, order=order, amount=0))

    assert row is None
    assert db.added == []

