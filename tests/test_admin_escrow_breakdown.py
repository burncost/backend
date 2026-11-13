"""Admin escrow fee/tax/commission breakdown + per-line vendor deduction.

Locks in the two halves of the escrow-visibility change:

  * `pricing_service` can report the client/vendor margin split **per line** (the
    escrow release pays vendors line by line) *without* moving the aggregate
    order-level figure the settlement ledger already depends on;
  * `release_order_escrow` transfers the line total MINUS that line's vendor-borne
    margin share, and the admin order-detail endpoint exposes the whole
    fee / tax / commission triangle the Escrow screen renders.

No database, no network: the async services/endpoint are driven with scripted
fakes, matching `test_pricing_margin.py` / `test_favorites.py`.
"""
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import asyncio

import pytest

from app.config import settings
from app.services import pricing_service as pricing
from app.services import runtime_settings

CLIENT = Decimal("0.40")
VENDOR = Decimal("0.60")


@pytest.fixture(autouse=True)
def clean_runtime_overrides(monkeypatch):
    """Isolate each test from DB-backed overrides + restore config defaults."""
    runtime_settings._cache.clear()
    runtime_settings.invalidate()
    monkeypatch.setattr(settings, "VAT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "VAT_RATE", "7.5", raising=False)
    monkeypatch.setattr(settings, "VAT_BASE", "subtotal_plus_margin", raising=False)
    monkeypatch.setattr(settings, "CLIENT_MARGIN_SHARE", str(CLIENT), raising=False)
    monkeypatch.setattr(settings, "VENDOR_MARGIN_SHARE", str(VENDOR), raising=False)
    monkeypatch.setattr(settings, "SETTLEMENT_COMMISSION_RATE", "0.0", raising=False)
    monkeypatch.setattr(settings, "SETTLEMENT_ENABLED", False, raising=False)
    yield
    runtime_settings._cache.clear()
    runtime_settings.invalidate()


# ── Fakes ───────────────────────────────────────────────────────────────────

class _FakeResult:
    """Just enough of a SQLAlchemy Result for the code under test."""

    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self


class _ScriptedSession:
    """Returns queued results in the order the code executes its queries."""

    def __init__(self, *results):
        self._results = list(results)
        self.queried = 0

    async def execute(self, _stmt):
        self.queried += 1
        if not self._results:
            raise AssertionError(f"unexpected extra query (#{self.queried})")
        return _FakeResult(self._results.pop(0))


class _BackgroundTasks:
    """Captures queued tasks instead of running them."""

    def __init__(self):
        self.calls = []

    def add_task(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))


class _FakePaymentService:
    """Records the transfer it would have made."""

    def __init__(self):
        self.mock_mode = True
        self.transfers = []

    async def create_transfer(self, amount, recipient_code, reference, reason):
        self.transfers.append({
            "amount": amount,
            "recipient_code": recipient_code,
            "reference": reference,
            "reason": reason,
        })
        return {"success": True}


def _percent_category(category_id="c-pct", margin=Decimal("5.00")):
    return SimpleNamespace(
        id=category_id, fee_model="percentage",
        platform_margin=margin, fee_fixed=Decimal("0"),
    )


def _item_line(product_id, unit_price, quantity, *, item_id=None, vendor_id="vendor-1"):
    """An OrderItem-shaped line (same attributes the services actually read)."""
    return SimpleNamespace(
        id=item_id or product_id,
        product_id=product_id,
        product_name=f"Product {product_id}",
        sku=f"SKU-{product_id}",
        quantity=quantity,
        unit_price=Decimal(str(unit_price)),
        total_price=Decimal(str(unit_price)) * quantity,
        vendor_id=vendor_id,
        vendor_status="pending",
        vendor=None,
    )


# ── pricing_service: per-line vs aggregate split ────────────────────────────

def test_item_key_prefers_the_row_id_and_falls_back_to_the_position():
    assert pricing.item_key(SimpleNamespace(id="line-1"), 3) == "line-1"
    assert pricing.item_key({"id": "line-2"}, 0) == "line-2"
    assert pricing.item_key({}, 0) == "idx:0"


def test_vendor_margins_by_item_splits_each_line_on_its_own_margin():
    """Every line carries the deduction that belongs to it, not a share of the total."""
    lines = [_item_line("p-a", 1000, 10), _item_line("p-b", 5000, 2)]
    db = _ScriptedSession(
        [("p-a", "c-pct"), ("p-b", "c-pct")],   # Product.id, Product.category_id
        [_percent_category()],
    )

    margins = asyncio.run(pricing.vendor_margins_by_item(db, lines))

    # 5% of 10,000 = 500 margin -> 60% vendor share = 300 (same for the 5,000 x 2 line).
    assert margins == {"p-a": Decimal("300.00"), "p-b": Decimal("300.00")}


def test_vendor_margins_by_item_is_zero_safe_for_unresolvable_products():
    lines = [_item_line("p-missing", 1000, 1)]
    db = _ScriptedSession([], [])
    margins = asyncio.run(pricing.vendor_margins_by_item(db, lines))
    # Falls back to the default 5% margin on the effective price.
    assert margins == {"p-missing": Decimal("30.00")}


def test_per_line_and_aggregate_splits_can_differ_by_rounding():
    """Why BOTH helpers exist: splitting each line then summing is not the same as
    summing then splitting. The aggregate keeps the settlement ledger's old figure."""
    lines = [_item_line(f"p-{n}", 1000, 1) for n in range(3)]

    def _session():
        return _ScriptedSession(
            [("p-0", "c-tiny"), ("p-1", "c-tiny"), ("p-2", "c-tiny")],
            [SimpleNamespace(id="c-tiny", fee_model="fixed", platform_margin=Decimal("0"),
                             fee_fixed=Decimal("0.01"))],
        )

    per_line = asyncio.run(pricing.vendor_margins_by_item(_session(), lines))
    aggregate = asyncio.run(pricing.vendor_margin_for_items(_session(), lines))

    assert sum(per_line.values()) == Decimal("0.03")   # 0.01 x 3 lines, each split alone
    assert aggregate == Decimal("0.02")                # 60% of the 0.03 total, rounded once


def test_order_pricing_breakdown_restates_items_fees_and_vat():
    order = SimpleNamespace(
        items=[_item_line("p-a", 1000, 10)],
        shipping_fee=Decimal("1500"),
        discount_amount=Decimal("0"),
        promo_discount=Decimal("0"),
    )
    db = _ScriptedSession([("p-a", "c-pct")], [_percent_category()])

    result, client_share, vendor_share = asyncio.run(
        pricing.order_pricing_breakdown(db, order)
    )

    assert result.subtotal == Decimal("10000.00")
    assert result.platform_fee == Decimal("200.00")      # 40% of the 500 margin
    assert result.vendor_fee == Decimal("300.00")        # 60% of the 500 margin
    assert result.platform_margin_total == Decimal("500.00")
    assert result.vat_amount == Decimal("765.00")        # 7.5% of (10,000 + 200)
    assert result.total_amount == Decimal("12465.00")    # + 1,500 shipping
    assert (client_share, vendor_share) == (CLIENT, VENDOR)
    assert result.breakdown[0]["fee_model"] == "percentage"
    assert result.breakdown[0]["vendor_fee"] == 300.0


# ── escrow release: the vendor share is actually deducted ───────────────────

def _patch_margins(monkeypatch, value="300.00"):
    async def _margins(_db, items):
        return {pricing.item_key(i, n): Decimal(value) for n, i in enumerate(items)}
    monkeypatch.setattr(pricing, "vendor_margins_by_item", _margins)


def test_release_deducts_the_vendor_share_from_each_line(monkeypatch):
    from app.api.v1.endpoints import orders

    _patch_margins(monkeypatch, "300.00")
    order = SimpleNamespace(order_number="ORD-1", items=[_item_line("p-a", 1000, 10)])
    tasks = _BackgroundTasks()

    asyncio.run(orders.release_order_escrow(tasks, order, None))

    assert len(tasks.calls) == 1
    _func, _args, kwargs = tasks.calls[0]
    assert kwargs["amount"] == 9700.0            # net of the supplier's share
    assert kwargs["gross_amount"] == 10000.0
    assert kwargs["platform_deduction"] == 300.0
    assert kwargs["order_number"] == "ORD-1"
    assert kwargs["vendor_id"] == "vendor-1"


def test_release_is_awaited_by_its_callers():
    """`release_order_escrow` became async (it reads the margin from the DB), so a
    forgotten `await` would silently release nothing."""
    import inspect

    from app.api.v1.endpoints import orders

    assert inspect.iscoroutinefunction(orders.release_order_escrow)


def test_release_skips_lines_without_a_vendor(monkeypatch):
    from app.api.v1.endpoints import orders

    _patch_margins(monkeypatch, "300.00")
    order = SimpleNamespace(
        order_number="ORD-2",
        items=[_item_line("p-a", 1000, 10, vendor_id=None)],
    )
    tasks = _BackgroundTasks()

    asyncio.run(orders.release_order_escrow(tasks, order, None))

    assert tasks.calls == []


def test_release_never_queues_a_negative_transfer(monkeypatch):
    from app.api.v1.endpoints import orders

    _patch_margins(monkeypatch, "999999.00")
    order = SimpleNamespace(order_number="ORD-3", items=[_item_line("p-a", 1000, 10)])
    tasks = _BackgroundTasks()

    asyncio.run(orders.release_order_escrow(tasks, order, None))

    _func, _args, kwargs = tasks.calls[0]
    assert kwargs["amount"] == 0.0
    assert kwargs["platform_deduction"] == 10000.0


def test_release_still_pays_the_line_when_the_margin_recompute_fails(monkeypatch):
    """A pricing hiccup must degrade to the old behaviour, never block the payout."""
    from app.api.v1.endpoints import orders

    async def _boom(_db, _items):
        raise RuntimeError("pricing unavailable")

    monkeypatch.setattr(pricing, "vendor_margins_by_item", _boom)
    order = SimpleNamespace(order_number="ORD-4", items=[_item_line("p-a", 1000, 10)])
    tasks = _BackgroundTasks()

    asyncio.run(orders.release_order_escrow(tasks, order, None))

    _func, _args, kwargs = tasks.calls[0]
    assert kwargs["amount"] == 10000.0
    assert kwargs["platform_deduction"] == 0.0


def test_orders_module_imports_the_payment_service_it_calls():
    """Regression: `PaymentService` was referenced but never imported, so every
    escrow transfer raised NameError inside the broad `except` and no money moved."""
    from app.api.v1.endpoints import orders
    from app.services.payment_service import PaymentService

    assert orders.PaymentService is PaymentService


def test_transfer_sends_the_net_amount_and_records_the_deduction(monkeypatch):
    from app.api.v1.endpoints import orders

    fake = _FakePaymentService()
    monkeypatch.setattr(orders, "PaymentService", lambda: fake)
    db = _ScriptedSession([SimpleNamespace(id="vendor-1", bank_account_number="0123456789")])

    asyncio.run(orders._release_escrow_to_vendor(
        vendor_id="vendor-1",
        order_number="ORD-1",
        amount=9700.0,
        db_session=db,
        gross_amount=10000.0,
        platform_deduction=300.0,
    ))

    assert fake.transfers == [{
        "amount": 9700.0,
        "recipient_code": "VENDOR-6789",
        "reference": "TRF-ORD-1-vendor-1",
        "reason": "Escrow release - Order ORD-1 (net of \u20a6300.00 platform fee on \u20a610,000.00 gross)",
    }]


def test_transfer_is_blocked_without_bank_details(monkeypatch):
    from app.api.v1.endpoints import orders

    fake = _FakePaymentService()
    monkeypatch.setattr(orders, "PaymentService", lambda: fake)
    db = _ScriptedSession([SimpleNamespace(id="vendor-1", bank_account_number=None)])

    asyncio.run(orders._release_escrow_to_vendor(
        vendor_id="vendor-1", order_number="ORD-1", amount=9700.0, db_session=db,
    ))

    assert fake.transfers == []


# ── admin order detail: the breakdown the Escrow screen renders ─────────────

def test_admin_order_detail_exposes_the_fee_tax_and_commission_breakdown(monkeypatch):
    from app.api.v1.endpoints import admin_phase1

    async def _no_refresh(_db):
        return None

    # The endpoint refreshes the DB-backed pricing overrides first; skip that
    # round-trip so the scripted query order below stays deterministic.
    monkeypatch.setattr(runtime_settings, "refresh", _no_refresh)

    item = _item_line("p-a", 1000, 10, vendor_id="vendor-1")
    order = SimpleNamespace(
        id="order-1", order_number="ORD-1", status="delivered",
        payment_status="completed", payment_method="card",
        subtotal=Decimal("10000.00"), shipping_fee=Decimal("0"),
        tax_amount=Decimal("765.00"), discount_amount=Decimal("0"),
        promo_discount=Decimal("0"), total_amount=Decimal("10965.00"),
        created_at=None, estimated_delivery_date=None, delivered_at=None,
        shipping_address=None, user=None, items=[item],
    )
    job = SimpleNamespace(
        id="job-1", order_id="order-1", job_number="JOB-1", status="delivered",
        driver_profile_id="drv-1", driver_name="Fleet Driver", driver_phone="08030000000",
        delivery_fee=Decimal("1500.00"),
    )
    db = _ScriptedSession(
        [order],                              # the order itself
        [("p-a", "c-pct")],                   # Product.id, Product.category_id
        [_percent_category()],                # Category (fee model)
        [job],                                # DeliveryJob
        [SimpleNamespace(id="drv-1", vendor_id="vendor-1")],   # fleet driver
        [],                                   # DriverEarnings (none accrued)
    )

    data = asyncio.run(admin_phase1.admin_order_detail(
        UUID("11111111-1111-1111-1111-111111111111"), {}, db
    ))

    # Buyer side: what was actually charged.
    assert data["promo_discount"] == 0.0
    assert data["charged"]["platform_fee"] == 200.0
    assert data["charged"]["total_amount"] == 10965.0

    # Supplier side: per-line split, and the net that leaves escrow.
    assert data["items"][0]["vendor_fee"] == 300.0
    assert data["items"][0]["client_fee"] == 200.0
    assert data["items"][0]["net_transfer"] == 9700.0
    assert data["pricing"]["vendor_fee"] == 300.0
    assert data["pricing"]["breakdown"][0]["vendor_fee"] == 300.0
    assert data["pricing"]["matches_current_config"] is True

    # Tax.
    assert data["tax"]["vat_amount_charged"] == 765.0
    assert data["tax"]["vat_base"] == "subtotal_plus_margin"

    # Driver leg: fleet drivers are paid by their vendor, not by us.
    assert data["driver"]["is_fleet"] is True
    assert data["driver"]["delivery_fee"] == 1500.0
    assert data["driver"]["commission_amount"] == 0.0
    assert data["driver"]["net_amount"] == 1500.0
    assert data["driver"]["earnings_status"] is None

    # Reconciliation.
    assert data["distribution"]["supplier_net"] == 9700.0
    assert data["distribution"]["supplier_deduction"] == 300.0
    assert data["distribution"]["platform_fee_from_buyer"] == 200.0
    assert data["distribution"]["platform_total"] == 500.0      # 200 + 300 + 0 commission
    assert data["distribution"]["driver_net"] == 1500.0
    assert data["distribution"]["reconciles_to"] == 10200.0     # 10,965 - 765 VAT
    assert data["distribution"]["settlement_enabled"] is False
