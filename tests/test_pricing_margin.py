"""Platform-margin infusion + VAT + settlement commission invariants.

These lock in the pricing decisions so a later refactor cannot silently:
  * ignore a category's fee model (the fixed-fee / service categories used to be
    mis-priced as percentages — they were all charged `total_price x margin%`),
  * charge margin on the struck-through price instead of the effective selling price,
  * lose or double-count money in the client/vendor 40/60 split,
  * re-hardcode the VAT rate or the VAT base.

Pure unit tests: no database, no network.
"""
from decimal import Decimal

import asyncio

import pytest

from app.config import settings
from app.services import pricing_service as pricing
from app.services import runtime_settings, settlement_service

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
    yield
    runtime_settings._cache.clear()
    runtime_settings.invalidate()


def _fixed(category_id="c-fixed", fee=Decimal("200")):
    return pricing.CategoryFee(category_id, "fixed", Decimal("1.50"), fee)


def _percent(category_id="c-pct", margin=Decimal("5.00")):
    return pricing.CategoryFee(category_id, "percentage", margin, Decimal("0"))


def _service(category_id="c-svc", margin=Decimal("6.50")):
    return pricing.CategoryFee(category_id, "service", margin, Decimal("0"))


def _item(category_id, unit_price, quantity):
    return {
        "product_id": f"p-{category_id}",
        "category_id": category_id,
        "product_name": "Item",
        "quantity": quantity,
        "unit_price": unit_price,
        "total_price": Decimal(str(unit_price)) * quantity,
    }


# ── fee_model ───────────────────────────────────────────────────────────────

def test_fixed_fee_category_charges_the_flat_fee_per_unit():
    """Cement-style categories bill a flat NGN amount per unit, not a percent."""
    assert pricing.compute_item_margin(Decimal("10000"), 10, _fixed()) == Decimal("2000.00")


def test_percentage_category_margin_uses_the_effective_selling_price():
    """Option B: 5% of what the buyer actually pays (the discounted price)."""
    # 20 units at the effective price of NGN 9,000 (base price was NGN 9,999).
    assert pricing.compute_item_margin(Decimal("9000"), 20, _percent()) == Decimal("9000.00")


def test_service_category_is_priced_exactly_like_percentage():
    service = pricing.compute_item_margin(Decimal("100000"), 1, _service())
    percentage = pricing.compute_item_margin(Decimal("100000"), 1, _percent(margin=Decimal("6.50")))
    assert service == percentage == Decimal("6500.00")


def test_unknown_category_falls_back_to_the_default_percentage_margin():
    result = pricing.compute_order_pricing([_item("missing", Decimal("1000"), 1)], {})
    # Default is 5% of the effective selling price.
    assert result.platform_margin_total == Decimal("50.00")


# ── client / vendor split ───────────────────────────────────────────────────

def test_client_and_vendor_shares_always_reconcile_to_the_total_margin():
    cfg = pricing.load_pricing_config()
    for total in ("0.01", "2000.00", "333.33", "99999.99"):
        client, vendor = pricing.split_margin(Decimal(total), cfg)
        assert client + vendor == Decimal(total).quantize(Decimal("0.01"))


def test_default_split_is_forty_sixty():
    cfg = pricing.load_pricing_config()
    assert cfg.client_share == CLIENT
    assert cfg.vendor_share == VENDOR


def test_order_splits_the_margin_between_the_buyer_and_the_vendor():
    result = pricing.compute_order_pricing(
        [_item("c-pct", Decimal("1000"), 10)],   # margin = 5% of 10,000 = 500
        {"c-pct": _percent()},
    )
    assert result.platform_margin_total == Decimal("500.00")
    assert result.platform_fee == Decimal("200.00")    # 40% charged to the buyer
    assert result.vendor_fee == Decimal("300.00")      # 60% borne by the vendor
    assert result.platform_fee + result.vendor_fee == result.platform_margin_total


def test_margin_split_normalises_when_shares_do_not_sum_to_one(monkeypatch):
    monkeypatch.setattr(settings, "CLIENT_MARGIN_SHARE", "1", raising=False)
    monkeypatch.setattr(settings, "VENDOR_MARGIN_SHARE", "3", raising=False)
    client, vendor = settlement_service.margin_split()
    assert client == Decimal("0.25")
    assert vendor == Decimal("0.75")


# ── VAT ─────────────────────────────────────────────────────────────────────

def test_vat_is_charged_on_subtotal_plus_the_buyers_platform_fee():
    result = pricing.compute_order_pricing(
        [_item("c-pct", Decimal("1000"), 10)],
        {"c-pct": _percent()},
    )
    assert result.vat_base == "subtotal_plus_margin"
    assert result.vat_base_amount == Decimal("10200.00")   # 10,000 + 200
    assert result.vat_amount == Decimal("765.00")          # 7.5%


def test_vat_base_can_be_restricted_to_the_subtotal(monkeypatch):
    monkeypatch.setattr(settings, "VAT_BASE", "subtotal", raising=False)
    result = pricing.compute_order_pricing(
        [_item("c-pct", Decimal("1000"), 10)],
        {"c-pct": _percent()},
    )
    assert result.vat_base_amount == Decimal("10000.00")
    assert result.vat_amount == Decimal("750.00")


def test_vat_can_be_switched_off_entirely(monkeypatch):
    monkeypatch.setattr(settings, "VAT_ENABLED", False, raising=False)
    result = pricing.compute_order_pricing(
        [_item("c-pct", Decimal("1000"), 10)],
        {"c-pct": _percent()},
    )
    assert result.vat_amount == Decimal("0.00")


def test_vat_rate_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "VAT_RATE", "10", raising=False)
    result = pricing.compute_order_pricing(
        [_item("c-pct", Decimal("1000"), 10)],
        {"c-pct": _percent()},
    )
    assert result.vat_amount == Decimal("1020.00")   # 10% of 10,200


# ── totals ──────────────────────────────────────────────────────────────────

def test_total_is_the_sum_of_its_parts():
    result = pricing.compute_order_pricing(
        [_item("c-pct", Decimal("1000"), 10), _item("c-fixed", Decimal("5000"), 2)],
        {"c-pct": _percent(), "c-fixed": _fixed()},
        shipping_fee=Decimal("1500"),
        discount_amount=Decimal("250"),
        promo_discount=Decimal("100"),
    )
    expected = (
        result.subtotal + result.platform_fee + result.shipping_fee
        + result.vat_amount - result.discount_amount - result.promo_discount
    )
    assert result.total_amount == expected


def test_mixed_basket_honours_each_categories_fee_model():
    result = pricing.compute_order_pricing(
        [_item("c-pct", Decimal("1000"), 10), _item("c-fixed", Decimal("5000"), 2)],
        {"c-pct": _percent(), "c-fixed": _fixed()},
    )
    # 5% of 10,000 = 500 ; fixed: 200 x 2 = 400
    assert result.platform_margin_total == Decimal("900.00")


# ── settlement commission reconciliation ────────────────────────────────────

def test_commission_rate_is_a_fraction_and_defaults_to_zero():
    """`commission_rate()` is a fraction (0.05 = 5%) — distinct from the category
    `platform_margin`, which is a percent. It defaults to 0 because the margin is
    already infused into the selling price at checkout."""
    assert settlement_service.commission_rate() == Decimal("0.0")


def test_fixed_fee_total_margin_matches_quantity_maths():
    """Regression: the fixed-fee categories used to be charged `total_price x margin%`."""
    result = pricing.compute_order_pricing(
        [_item("c-fixed", Decimal("10000"), 10)],   # 10 bags
        {"c-fixed": _fixed()},
    )
    assert result.platform_margin_total == Decimal("2000.00")   # ₦200 x 10, not 1.5% x 100k
    assert result.platform_fee == Decimal("800.00")
    assert result.vendor_fee == Decimal("1200.00")


# ── settlement ledger accrual (Phase 3) ─────────────────────────────────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeSession:
    """Just enough of AsyncSession for the idempotent accrual path."""

    def __init__(self):
        self.added = []

    async def execute(self, _stmt):
        return _FakeResult([])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _accrue(gross, gateway_fee="0", vendor_margin="0"):
    svc = settlement_service.SettlementService()
    db = _FakeSession()
    entry = asyncio.run(svc.accrue_order(
        db, vendor_id="vendor-1", order_id="order-1",
        gross_amount=Decimal(gross),
        gateway_fee=Decimal(gateway_fee),
        vendor_margin=Decimal(vendor_margin),
    ))
    return db, entry


def test_settlement_is_disabled_by_default():
    assert settlement_service.is_enabled() is False


def test_settlement_flag_is_db_overridable():
    runtime_settings._cache["settlement_enabled"] = "true"
    try:
        assert settlement_service.is_enabled() is True
    finally:
        runtime_settings._cache.pop("settlement_enabled", None)


def test_accrual_is_a_no_op_while_settlement_is_disabled():
    _db, entry = _accrue("10000", "150", "300")
    assert entry is None


def test_accrual_deducts_the_vendor_margin_share_from_the_payout():
    runtime_settings._cache["settlement_enabled"] = "true"
    try:
        _db, entry = _accrue("10000", "150", "300")
        assert entry is not None
        # gross - gateway_fee - commission(0) - vendor_margin
        assert entry.net_amount == Decimal("9550.00")
        assert entry.commission_amount == Decimal("0.00")
    finally:
        runtime_settings._cache.pop("settlement_enabled", None)


def test_accrual_without_a_vendor_margin_is_backwards_compatible():
    runtime_settings._cache["settlement_enabled"] = "true"
    try:
        _db, entry = _accrue("10000", "150", "0")
        assert entry.net_amount == Decimal("9850.00")
    finally:
        runtime_settings._cache.pop("settlement_enabled", None)


# ── payout provider selection (Phase 5) ─────────────────────────────────────

def test_payout_prefers_the_configured_primary_gateway(monkeypatch):
    from app.services import payouts

    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", "sk_test", raising=False)
    gateway, reason = payouts.resolve_gateway(None)
    assert (gateway, reason) == ("paystack", "primary")


def test_payout_falls_back_to_monnify_when_paystack_is_unconfigured(monkeypatch):
    from app.services import payouts

    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", None, raising=False)
    monkeypatch.setattr(settings, "MONNIFY_SECRET_KEY", "sk_mon", raising=False)
    monkeypatch.setattr(settings, "MONNIFY_PUBLIC_KEY", "pk_mon", raising=False)
    gateway, reason = payouts.resolve_gateway(None)
    assert (gateway, reason) == ("monnify", "backup")


def test_payout_reports_unconfigured_rather_than_silently_skipping(monkeypatch):
    from app.services import payouts

    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", None, raising=False)
    monkeypatch.setattr(settings, "MONNIFY_SECRET_KEY", None, raising=False)
    monkeypatch.setattr(settings, "MONNIFY_PUBLIC_KEY", None, raising=False)
    gateway, reason = payouts.resolve_gateway(None)
    assert reason == "unconfigured"
    assert gateway in payouts.VALID_GATEWAYS


def test_an_explicit_gateway_wins_when_it_is_configured(monkeypatch):
    from app.services import payouts

    monkeypatch.setattr(settings, "MONNIFY_SECRET_KEY", "sk_mon", raising=False)
    monkeypatch.setattr(settings, "MONNIFY_PUBLIC_KEY", "pk_mon", raising=False)
    gateway, reason = payouts.resolve_gateway("monnify")
    assert (gateway, reason) == ("monnify", "requested")
