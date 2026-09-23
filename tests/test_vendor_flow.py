"""
Basic test scaffold for vendor flow.
Run with: pytest tests/test_vendor_flow.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from decimal import Decimal


def test_vendor_tier_caps_match_spec():
    """starter ₦3M · verified_vendor ₦10M · enterprise uncapped."""
    from app.services.vendor_tier_service import TIER_CAPS, tier_cap
    assert TIER_CAPS["starter"] == 3_000_000
    assert TIER_CAPS["verified_vendor"] == 10_000_000
    assert TIER_CAPS["enterprise"] is None
    assert tier_cap("starter") == 3_000_000
    assert tier_cap("verified_vendor") == 10_000_000
    assert tier_cap("enterprise") is None


def test_vendor_tier_computed_from_data():
    from app.services.vendor_tier_service import compute_tier
    v = MagicMock()
    v.nin = ""
    v.cac_business_registration_number = ""
    assert compute_tier(v, None) == "starter"
    v.nin = "12345678901"
    assert compute_tier(v, None) == "verified_vendor"
    v.cac_business_registration_number = "RC-123456"
    assert compute_tier(v, None) == "enterprise"


def test_vendor_tier_progression_monotonic_and_enterprise_verifies():
    from app.services.vendor_tier_service import resolve_and_apply
    v = MagicMock()
    v.nin = "12345678901"
    v.cac_business_registration_number = "RC-123456"
    v.verification_tier = "starter"
    v.verification_status = None
    resolve_and_apply(v, None)
    assert v.verification_tier == "enterprise"
    assert v.verification_status == "verified"


def test_vendor_tier_legacy_codes_normalize():
    from app.services.vendor_tier_service import normalize_tier
    assert normalize_tier("cac_only") == "starter"
    assert normalize_tier("tier_1") == "starter"
    assert normalize_tier("documented") == "verified_vendor"
    assert normalize_tier("tier_2") == "verified_vendor"
    assert normalize_tier("trusted") == "enterprise"
    assert normalize_tier("tier_3") == "enterprise"
    assert normalize_tier("starter") == "starter"
    assert normalize_tier(None) == "starter"
    assert normalize_tier("") == "starter"


# ---- Mono identity verification match rules ----

MONO_NIN_SAMPLE = {
    "firstname": "WIGO",
    "surname": "MUSA",
    "birthdate": "01-01-1990",  # Mono format: DD-MM-YYYY
    "nin": "12345678901",
}


def _profile(first="Wigo", last="Musa", dob="1990-01-01", other=""):
    p = MagicMock()
    p.first_name = first
    p.last_name = last
    p.other_name = other
    from datetime import date
    p.date_of_birth = date.fromisoformat(dob)
    return p


def test_nin_matches_on_name_and_dob():
    from app.services.mono_identity_service import nin_matches
    assert nin_matches(MONO_NIN_SAMPLE, _profile()) is True
    # Case/whitespace insensitive
    assert nin_matches(MONO_NIN_SAMPLE, _profile(first="  WIGO ", last="musa")) is True


def test_nin_mismatch_on_name():
    from app.services.mono_identity_service import nin_matches
    assert nin_matches(MONO_NIN_SAMPLE, _profile(first="John")) is False


def test_nin_mismatch_on_dob():
    from app.services.mono_identity_service import nin_matches
    assert nin_matches(MONO_NIN_SAMPLE, _profile(dob="1991-01-01")) is False


def test_nin_mismatch_on_missing_profile():
    from app.services.mono_identity_service import nin_matches
    assert nin_matches(MONO_NIN_SAMPLE, None) is False


def test_nin_soft_middle_name_check():
    """Other name (middle name) is a soft check: fails on mismatch, skipped when one side is empty."""
    from app.services.mono_identity_service import nin_matches
    sample = {**MONO_NIN_SAMPLE, "middlename": "SAMUEL"}
    # Both present and equal (case/whitespace insensitive)
    assert nin_matches(sample, _profile(other="samuel")) is True
    # Both present but different -> fails
    assert nin_matches(sample, _profile(other="Chinedu")) is False
    # User has no other name -> check skipped
    assert nin_matches(sample, _profile(other="")) is True
    # Mono returned no middlename -> check skipped
    assert nin_matches(MONO_NIN_SAMPLE, _profile(other="Chinedu")) is True


def _vendor(business_name="Dei-Dei Cement & Steel Depot"):
    v = MagicMock()
    v.business_name = business_name
    return v


def test_cac_matches_on_name_and_active():
    from app.services.mono_identity_service import cac_matches
    result = {"business_name": "dei-dei cement & steel depot", "status": "active"}
    assert cac_matches(result, _vendor()) is True


def test_cac_mismatch_on_name():
    from app.services.mono_identity_service import cac_matches
    result = {"business_name": "Some Other Ltd", "status": "active"}
    assert cac_matches(result, _vendor()) is False


def test_cac_mismatch_on_inactive():
    from app.services.mono_identity_service import cac_matches
    result = {"business_name": "Dei-Dei Cement & Steel Depot", "status": "inactive"}
    assert cac_matches(result, _vendor()) is False


def test_nin_cache_roundtrip():
    from app.services.mono_identity_service import get_cached_nin, save_nin_cache
    p = _profile()
    save_nin_cache(p, MONO_NIN_SAMPLE)
    assert get_cached_nin(p, "12345678901") == MONO_NIN_SAMPLE
    assert get_cached_nin(p, "99999999999") is None


def test_vendor_onboarding_validation():
    """Verify vendor onboarding requires mandatory fields."""
    from pydantic import ValidationError
    from app.schemas.vendor import VendorCreate
    with pytest.raises(ValidationError):
        # Pydantic v2: omit all required fields to trigger validation error
        # (empty strings pass validation; omitting fields with no defaults triggers Missing)
        VendorCreate.model_validate({})


def test_order_total_calculation():
    """Verify order total = subtotal + platform_fee + shipping + VAT - discount."""
    subtotal = 250_000.00
    platform_fee = 18_750.00    # 7.5% avg across categories
    shipping_fee = 5_000.00     # dev mode flat rate
    vat = (subtotal + platform_fee) * 0.075  # 20,156.25
    discount = 0.00
    total = subtotal + platform_fee + shipping_fee + vat - discount
    assert round(total, 2) == 293_906.25


def test_shipping_service_dev_mode():
    """Verify dev mode returns flat rate."""
    with patch("app.config.settings.DEBUG", True):
        from app.services.shipping_service import ShippingService
        import asyncio
        async def run():
            service = ShippingService()
            result = await service.calculate_shipping(
                db=MagicMock(),
                vendor_id=uuid4(),
                items=[{"base_price": 5000, "discount_price": 4500, "quantity": 10}],
            )
            assert result["shipping_fee"] == 5000.00
            assert result["breakdown"]["zone"] == "dev_flat_rate"
        asyncio.run(run())


def test_platform_margin_calculation():
    """Verify per-category margin calculation."""
    item_subtotal = 50000.00
    margin_pct = 10.00  # 10%
    expected_margin = round(item_subtotal * margin_pct / 100, 2)
    assert expected_margin == 5000.00


def test_vendor_status_endpoint_response():
    """Verify vendor status response contains required fields."""
    expected_fields = [
        "verification_status",
        "rating",
        "total_reviews",
        "total_sales",
        "is_featured",
    ]
    mock_response = {
        "verification_status": "verified",
        "verification_date": "2026-07-15T00:00:00",
        "rating": 4.5,
        "total_reviews": 25,
        "total_sales": 1500000.00,
        "is_featured": True,
    }
    for field in expected_fields:
        assert field in mock_response


def test_demand_alerts_matching():
    """Verify demand alerts match vendor's city."""
    vendor_city = "Abuja"
    alerts = [
        {"city": "Abuja", "item_description": "Dangote Cement"},
        {"city": "Lagos", "item_description": "BUA Cement"},
        {"city": "Abuja", "item_description": "Steel Rebar"},
    ]
    matching = [a for a in alerts if a["city"] == vendor_city]
    assert len(matching) == 2
    assert matching[0]["item_description"] == "Dangote Cement"
    assert matching[1]["item_description"] == "Steel Rebar"


def test_order_status_transitions():
    """Verify valid order status transitions."""
    valid = {
        "pending": ["confirmed", "cancelled"],
        "confirmed": ["processing", "cancelled"],
        "processing": ["shipped", "cancelled"],
        "shipped": ["in_transit", "delivered", "cancelled"],
        "in_transit": ["delivered", "cancelled"],
        "delivered": [],
        "cancelled": [],
    }
    # Valid transition
    assert "confirmed" in valid["pending"]
    # Invalid transition
    assert "delivered" not in valid["pending"]
    # Terminal state
    assert len(valid["delivered"]) == 0


# ---- Settings screen: notification prefs + primary bank selection ----


def test_notification_preferences_defaults_and_partial_updates():
    """Defaults match the settings UI; partial payloads only touch provided keys."""
    from app.api.v1.endpoints.users import NotificationPreferences
    defaults = NotificationPreferences().model_dump()
    assert defaults["new_orders"] is True
    assert defaults["email_notifications"] is True
    assert defaults["promotions"] is False
    assert defaults["sms_notifications"] is False

    partial = NotificationPreferences.model_validate({"email_notifications": False})
    assert partial.model_dump(exclude_unset=True) == {"email_notifications": False}


def test_newest_primary_bank_picks_latest():
    """Legacy vendors can have several is_primary rows — the newest must win."""
    from datetime import datetime
    from app.api.v1.endpoints.vendors import _newest_primary_bank

    class _Account:
        def __init__(self, name, primary, created):
            self.bank_name = name
            self.is_primary = primary
            self.created_at = created

    vendor = MagicMock()
    vendor.bank_accounts = [
        _Account("First Bank", True, datetime(2024, 1, 1)),
        _Account("GTBank", True, datetime(2026, 9, 21)),
        _Account("UBA", False, datetime(2030, 1, 1)),
    ]
    assert _newest_primary_bank(vendor).bank_name == "GTBank"

    vendor.bank_accounts = []
    assert _newest_primary_bank(vendor) is None


# ---- Logistics: route registry integrity ----


def test_delivery_routes_have_no_duplicate_path_and_method():
    """A duplicate path+method silently shadows the first handler (FastAPI serves
    the first match). This is exactly how GET /delivery/jobs/mine broke the driver
    dashboard: the vendor handler was registered first, so drivers got a 403.
    """
    from collections import defaultdict
    from app.api.v1.endpoints import deliveries

    seen = defaultdict(list)
    for route in deliveries.router.routes:
        for method in (getattr(route, "methods", None) or []):
            seen[(method, route.path)].append(getattr(route, "name", "?"))

    duplicates = {k: v for k, v in seen.items() if len(v) > 1}
    assert duplicates == {}, f"Duplicate routes shadow earlier handlers: {duplicates}"


def test_jobs_mine_is_role_aware_single_handler():
    """GET /jobs/mine must serve both vendors and drivers from one handler."""
    import inspect
    from app.api.v1.endpoints import deliveries

    src = inspect.getsource(deliveries.list_my_jobs)
    assert 'role == "driver"' in src
    assert 'role in ("vendor", "admin", "super_admin")' in src


def test_delivery_endpoints_used_by_the_vendor_logistics_tab_exist():
    """Every endpoint the logistics tab calls must be registered."""
    from app.api.v1.endpoints import deliveries

    registered = {(m, r.path) for r in deliveries.router.routes for m in (getattr(r, "methods", None) or [])}
    # Paths are router-relative; the /delivery prefix is added at include_router.
    expected = [
        ("GET", "/drivers"),
        ("GET", "/drivers/assignable"),
        ("POST", "/drivers/link"),
        ("POST", "/drivers/invite"),
        ("POST", "/jobs"),
        ("GET", "/jobs/mine"),
        ("POST", "/jobs/{job_id}/cancel"),
        ("GET", "/jobs/{job_id}/track"),
        ("GET", "/jobs/{job_id}/pod"),
    ]
    missing = [e for e in expected if e not in registered]
    assert missing == [], f"Missing logistics endpoints: {missing}"


# ---- Orders tab readiness ----


def test_vendor_update_order_requires_ownership():
    """A vendor may only transition orders containing their OWN items.

    The handler previously never compared the order's items against the caller, so
    any vendor could mark another vendor's order delivered/cancelled.
    """
    import inspect
    from app.api.v1.endpoints import orders

    src = inspect.getsource(orders.vendor_update_order)
    assert "for item in order.items" in src
    assert 'str(item.vendor_id) == str(vendor_id)' in src
    assert "HTTP_403_FORBIDDEN" in src
    # The ownership check must run BEFORE the status is mutated.
    assert src.index("This order is not from your catalog") < src.index("order.status = new_status")


def test_order_transitions_cover_every_actionable_status():
    """payment_failed / ready_for_pickup used to be absent, so orders in those
    states could never be advanced by a vendor."""
    import inspect
    from app.api.v1.endpoints import orders

    src = inspect.getsource(orders.vendor_update_order)
    for status in ("pending_payment", "payment_failed", "confirmed", "processing",
                   "ready_for_pickup", "shipped", "in_transit", "delivered",
                   "cancelled", "refunded"):
        assert f'"{status}"' in src, f"missing transition entry for {status}"


def test_order_list_exposes_vendor_share_not_just_order_total():
    """Orders tab amounts must be the vendor's own slice of a multi-vendor order."""
    import inspect
    from app.api.v1.endpoints import orders
    from app.schemas.order import OrderListUI

    src = inspect.getsource(orders.get_orders_ui)
    assert '"vendor_total"' in src
    assert '"item_count"' in src

    fields = OrderListUI.model_fields
    assert "vendor_total" in fields and "item_count" in fields
    # `total` stays as the full order value (SupplierAdmin labels it "Order Total").
    assert "total" in fields


# ---- Payments & reviews tab readiness ----


def test_payout_due_at_is_48h_after_delivery_confirmation():
    """Payout becomes due at most 48h after Order.delivered_at (set by both the
    buyer confirm-delivery flow and the driver mark-delivered flow)."""
    from datetime import datetime, timedelta
    from app.api.v1.endpoints import payments

    assert payments.PAYOUT_WINDOW_HOURS == 48

    class _Order:
        delivered_at = None

    assert payments.payout_due_at(_Order()) is None  # not delivered yet

    delivered = datetime(2026, 9, 21, 10, 0, 0)
    _Order.delivered_at = delivered
    assert payments.payout_due_at(_Order()) == delivered + timedelta(hours=48)


def test_payments_summary_buckets_payment_status_correctly():
    """Only 'completed' is earned, only 'pending' is awaiting payment, and failed /
    refunded money must never be reported as escrow."""
    from app.api.v1.endpoints.payments import PAYOUT_WINDOW_HOURS  # noqa: F401
    import inspect
    from app.api.v1.endpoints import payments

    src = inspect.getsource(payments.list_vendor_payments)
    # The summary splits the four buckets explicitly (previously everything that was
    # not 'completed' — including failed/refunded — was lumped into pending_balance).
    for key in ('"completed"', '"pending"', '"failed"', "refunded_amount", "awaiting_payout"):
        assert key in src, f"missing summary handling for {key}"
    assert "first day of next month" not in src


def test_vendor_payment_rows_use_the_vendor_share_not_the_order_total():
    """An order can contain several vendors' items, so a row's amount must be the
    vendor's own slice (was float(o.total_amount) — the whole order)."""
    import inspect
    from app.api.v1.endpoints import payments

    src = inspect.getsource(payments.list_vendor_payments)
    assert "share_by_order" in src
    assert "func.sum(OrderItem.total_price)" in src
    assert '"amount": float(o.total_amount)' not in src
    assert '"order_total": float(o.total_amount or 0)' in src


def test_vendor_reviews_return_previous_average_with_30_day_cutoff():
    import inspect
    from app.api.v1.endpoints import reviews

    src = inspect.getsource(reviews.list_vendor_product_reviews)
    assert "previous_average_rating" in src
    assert "timedelta(days=30)" in src


# ---- Sales analytics: window resolution, AOV consistency, change display ----


def test_analytics_window_accepts_presets_and_validates_custom_ranges():
    from datetime import datetime
    import pytest
    from fastapi import HTTPException
    from app.api.v1.endpoints import analytics

    # Presets map to the documented day counts.
    assert analytics._period_days("7d") == 7
    assert analytics._period_days("30d") == 30
    assert analytics._period_days("90d") == 90
    assert analytics._period_days("1y") == 365
    assert analytics._period_days("nonsense") == 30  # safe fallback

    start, end = analytics._resolve_window("30d", None, None)
    assert (end - start).days == 30

    # A custom range is inclusive of the end date (runs to the next midnight).
    start, end = analytics._resolve_window("30d", "2026-08-01", "2026-08-21")
    assert start == datetime(2026, 8, 1)
    assert end == datetime(2026, 8, 22)

    # A window ending today is clamped to now, so future rows can never count.
    start, end = analytics._resolve_window("30d", "2026-08-01", "2026-12-31")
    assert end <= datetime.utcnow()

    for args in (
        ("30d", "2026-09-21", "2026-09-01"),   # inverted
        ("30d", "2026-09-01", None),           # half a range
        ("30d", None, "2026-09-21"),           # half a range
        ("30d", "21/09/2026", "2026-09-22"),   # wrong format
        ("30d", "2020-01-01", "2026-09-21"),   # > 366 days
    ):
        with pytest.raises(HTTPException) as exc:
            analytics._resolve_window(*args)
        assert exc.value.status_code == 400


def test_average_order_value_uses_one_population():
    """AOV must divide delivered revenue by delivered orders only."""
    from app.api.v1.endpoints import analytics

    # 1 delivered order worth 500k: the old code divided by every order in the
    # window (including unpaid/cancelled), reporting 100k instead of 500k.
    assert analytics._aov(500_000, 1) == 500_000
    assert analytics._aov(1_000_000, 4) == 250_000
    assert analytics._aov(0, 0) == 0          # no division by zero
    assert analytics._aov(750_000, 0) == 0


def test_change_reports_new_instead_of_fake_growth():
    from app.api.v1.endpoints import analytics

    assert analytics._change(0, 0) == {"value": 0.0, "display": "0%"}
    # No baseline -> "New", not a misleading +100%.
    assert analytics._change(500, 0) == {"value": None, "display": "New"}
    assert analytics._change(150, 100)["display"] == "+50.0%"
    assert analytics._change(150, 100)["value"] == 50.0
    assert analytics._change(50, 100)["display"] == "-50.0%"
    assert analytics._change(100, 100)["display"] == "+0.0%"


def test_bucket_granularity_and_week_alignment():
    from datetime import datetime
    from app.api.v1.endpoints import analytics

    # <= 92 day windows are bucketed daily, longer ones weekly (used by /sales).
    assert analytics._PRESET_DAYS["90d"] <= 92
    assert analytics._PRESET_DAYS["1y"] > 92

    # Weekly buckets align to ISO weeks (Monday).
    wednesday = datetime(2026, 9, 16, 13, 30)
    monday = analytics._bucket_start(wednesday, "week")
    assert monday.weekday() == 0
    assert monday == datetime(2026, 9, 14)

    day = analytics._bucket_start(wednesday, "day")
    assert day == datetime(2026, 9, 16)


# ---- Tier 2 upgrade: phone normalization + required personal details ----


def test_normalize_phone_matches_user_schema():
    """Phone is stored the same way UserUpdate does (+234…), for every input form."""
    from app.api.v1.endpoints.tiers import _normalize_phone
    assert _normalize_phone("08012345678") == "+2348012345678"
    assert _normalize_phone("+2348012345678") == "+2348012345678"
    assert _normalize_phone("2348012345678") == "+2348012345678"
    assert _normalize_phone("0801 234 5678") == "+2348012345678"
    # Invalid values are rejected (not silently stored)
    assert _normalize_phone("") is None
    assert _normalize_phone("12345") is None
    assert _normalize_phone("0801234567") is None      # too short
    assert _normalize_phone("01012345678") is None     # not a mobile prefix


def test_verified_vendor_requires_names_in_upgrade_handler():
    """The tier-2 handler must reject a missing first/last name before mutating."""
    import inspect
    from app.api.v1.endpoints import tiers
    src = inspect.getsource(tiers.upgrade_vendor_tier)
    assert "first and last name are required" in src
    # Names must be written unconditionally once validated (no truthy guards).
    assert "profile.first_name = first_name" in src
    assert "profile.last_name = last_name" in src
    # Validation happens before the NIN/CAC persistence block, so a rejection
    # cannot leave a partial write behind.
    assert src.index("first and last name are required") < src.index("vend.nin = nin")


# ---- Marketplace catalogue: server-side search / filter / sort ----

def test_product_list_accepts_name_sort_and_in_stock_filter():
    """The marketplace grid filters server-side; these params must be accepted."""
    import inspect

    from app.api.v1.endpoints import products as products_api
    from app.schemas.product import ProductFilter

    sig = inspect.signature(products_api.list_products)
    assert "in_stock" in sig.parameters

    # FastAPI keeps Query(..., regex=...) as pydantic field metadata (v2), so read the
    # allowed values from there instead of generating the whole OpenAPI schema.
    query_default = sig.parameters["sort_by"].default
    patterns = [getattr(m, "pattern", None) for m in getattr(query_default, "metadata", [])]
    pattern = next((p for p in patterns if p), "")
    assert "name" in pattern, "sort_by=name must be allowed (service already maps it)"
    for allowed in ("created_at", "price", "rating", "sales_count"):
        assert allowed in pattern

    assert "in_stock" in ProductFilter.model_fields

    src = inspect.getsource(products_api.list_products)
    assert "in_stock=in_stock" in src, "the query param must reach ProductFilter"


def test_product_service_applies_in_stock_and_supplier_search():
    """In Stock Only + supplier-name search are enforced in SQL, not just the UI."""
    import inspect

    from app.services.product_service import ProductService

    src = inspect.getsource(ProductService.list_products)
    assert "filters.in_stock" in src
    assert "Product.quantity > 0" in src
    # Supplier search via subquery: stays valid with and without the verified-vendor join.
    assert "Vendor.business_name.ilike" in src
    assert "Product.vendor_id.in_(vendor_match)" in src


def test_marketplace_category_pills_come_from_products_not_taxonomy():
    """Pills must mirror the products' categories: /categories returns 152 entries
    (incl. sub-categories), which rendered 153 filter buttons."""
    from pathlib import Path

    catalog = (
        Path(__file__).resolve().parents[2] / "Frontend" / "src" / "app"
        / "components" / "MarketplaceCatalog.tsx"
    ).read_text(encoding="utf-8")

    assert "categoryOptions" in catalog
    assert "getCategories" not in catalog, "pills must not use the full category taxonomy"
    # Derived from the default (unfiltered) page, so the row stays stable while filtering.
    assert "result.products.forEach" in catalog
    assert "setCategoryOptions(names)" in catalog
    # "All" clears the filter, and every pill resets pagination.
    assert 'selectedCategory === "All" ? undefined : selectedCategory' in catalog
    assert "setSelectedCategory(name); setPage(1);" in catalog


# ---- Demand alerts: 48h expiry + endpoint health ----

# ---- Dependency annotation / shape guards ----

def test_no_endpoint_misuses_a_dependency_shape():
    """`current_user` is an ORM User for the user deps and a dict for the vendor deps.

    Annotating it `dict` while it is an ORM object, or subscripting it, produced real
    HTTP 500s (TypeError: 'User' object is not subscriptable) in 15 endpoints — including
    the whole BOQ flow. This guard enforces the shapes documented in app/api/deps.py.
    """
    import ast
    import re
    from pathlib import Path

    orm_deps = {"get_current_user", "get_current_active_user", "get_current_admin",
                "get_optional_user", "require_roles"}
    dict_deps = {"get_current_vendor", "get_current_verified_vendor"}

    offenders = []
    endpoints = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints"
    for path in sorted(endpoints.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args, defaults = node.args.args, node.args.defaults
            pairs = list(zip(args[len(args) - len(defaults):], defaults)) if defaults else []
            body = ast.unparse(node)
            for arg, default in pairs:
                if not isinstance(default, ast.Call) or not default.args:
                    continue
                dep = ast.unparse(default.args[0]).split("(")[0].strip()
                annotation = ast.unparse(arg.annotation) if arg.annotation else ""
                subscripted = f"{arg.arg}[" in body or f"{arg.arg}.get(" in body
                where = f"{path.name}:{node.lineno} {node.name}({arg.arg})"
                if dep in orm_deps and subscripted:
                    offenders.append(f"{where}: subscripts ORM dependency {dep}")
                if dep in orm_deps and annotation == "dict":
                    offenders.append(f"{where}: annotated dict but {dep} returns a User ORM object")
                if dep in dict_deps and re.search(rf"\b{arg.arg}\.(?!get\()", body):
                    offenders.append(f"{where}: attribute access on dict dependency {dep}")

    assert offenders == [], offenders


# ---- Vendor tier/risk wiring ----

def test_risk_score_improves_at_every_tier_milestone():
    """A tier-2 upgrade must visibly move the score: it previously changed nothing because
    the scorer still keyed on the pre-rework codes (trusted/documented/cac_only)."""
    from app.services.risk_service import compute_risk_score, risk_bucket

    starter = compute_risk_score("pending", "starter")
    tier2 = compute_risk_score("pending", "verified_vendor")
    tier3 = compute_risk_score("verified", "enterprise")

    assert (starter, tier2, tier3) == (70, 60, 10)
    assert starter > tier2 > tier3, "risk must decrease monotonically with each milestone"
    assert risk_bucket(starter) == "high"
    assert risk_bucket(tier2) == "medium", "tier 2 must leave the High bucket"
    assert risk_bucket(tier3) == "low"


def test_risk_score_normalises_legacy_tier_codes():
    """Old rows (trusted/documented/cac_only/tier_2) must keep their credit."""
    from app.services.risk_service import compute_risk_score

    assert compute_risk_score("pending", "documented") == compute_risk_score("pending", "verified_vendor")
    assert compute_risk_score("pending", "trusted") == compute_risk_score("pending", "enterprise")
    assert compute_risk_score("pending", "cac_only") == compute_risk_score("pending", "starter")
    assert compute_risk_score("pending", "tier_2") == compute_risk_score("pending", "verified_vendor")
    assert compute_risk_score("pending", "tier_3") == compute_risk_score("pending", "enterprise")
    assert compute_risk_score("pending", None) == compute_risk_score("pending", "starter")


def test_risk_score_tier_credit_is_tier_driven_not_status():
    import inspect

    from app.services import risk_service

    assert set(risk_service.TIER_RISK_CREDIT) == {"starter", "verified_vendor", "enterprise"}
    src = inspect.getsource(risk_service.compute_risk_score)
    assert "TIER_RISK_CREDIT" in src and "normalize_tier" in src
    assert "trusted" not in src and "documented" not in src, "legacy codes must be normalised, not hard-coded"


def test_tier_verified_status_stays_enterprise_only():
    """Guard against 'fixing' the risk score by marking tier 2 verified — that status
    drives the enterprise-only BurnCost Verified badge."""
    import inspect

    from app.api.v1.endpoints import tiers

    src = inspect.getsource(tiers.upgrade_vendor_tier)
    assert 'if target == "enterprise":' in src
    assert 'vend.verification_status = "verified"' in src


def test_vendor_status_bus_refreshes_every_tier_component():
    """The sidebar badge and the tier card fetch /vendors/me/status independently, so an
    upgrade in one must notify the other (otherwise the logo needed a page reload)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "Frontend" / "src" / "app"
    bus = (root / "utils" / "vendorStatusBus.ts").read_text(encoding="utf-8")
    assert "vendor-status-updated" in bus
    assert "export function notifyVendorStatusUpdated" in bus
    assert "export function onVendorStatusUpdated" in bus

    modal = (root / "components" / "supplier" / "UpgradeTierModal.tsx").read_text(encoding="utf-8")
    assert "notifyVendorStatusUpdated()" in modal, "a successful upgrade must announce itself"

    dashboard = (root / "components" / "supplier" / "SupplierDashboard.tsx").read_text(encoding="utf-8")
    card = (root / "components" / "supplier" / "SupplierTierCard.tsx").read_text(encoding="utf-8")
    assert "onVendorStatusUpdated(fetchVendorStatus)" in dashboard
    assert "onVendorStatusUpdated(loadStatus)" in card
    assert "__refreshVendorStatus" not in dashboard, "the ad-hoc window global is superseded"


def test_demand_alert_ttl_is_48_hours():
    """Every alert must disappear 48h after it was raised."""
    from datetime import datetime, timezone

    from app.models.demand_alert import DEMAND_ALERT_TTL_HOURS, demand_alert_cutoff

    assert DEMAND_ALERT_TTL_HOURS == 48
    now = datetime(2026, 1, 3, 12, 0, tzinfo=timezone.utc)
    assert demand_alert_cutoff(now) == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    # Timezone-aware, so it compares correctly against the timestamptz column.
    assert demand_alert_cutoff().tzinfo is not None


def test_demand_alert_listings_apply_the_ttl():
    """The vendor endpoint and the public listing must both hide expired alerts."""
    import inspect

    from app.api.v1.endpoints import demand_alerts as da_api
    from app.api.v1.endpoints import vendors as vendors_api

    vendor_src = inspect.getsource(vendors_api.get_demand_alerts)
    assert "demand_alert_cutoff()" in vendor_src
    # Count and data query must share the window, or the badge disagrees with the list.
    assert vendor_src.count("created_at >= :cutoff") == 2
    # City matching is case/whitespace insensitive.
    assert "LOWER(TRIM(city)) = LOWER(TRIM(:city))" in vendor_src

    public_src = inspect.getsource(da_api.list_demand_alerts)
    assert "DemandAlert.created_at >= demand_alert_cutoff()" in public_src


def test_vendor_demand_alerts_endpoint_loads_requesters_eagerly():
    """`user.profile` is a lazy relationship: touching it inside an async endpoint raised
    MissingGreenlet, so every alert that had a requester returned HTTP 500."""
    import inspect

    from app.api.v1.endpoints import vendors as vendors_api

    src = inspect.getsource(vendors_api.get_demand_alerts)
    assert "joinedload(User.profile)" in src
    # The old per-row lazy lookup (N+1 + MissingGreenlet) must be gone.
    assert "select(User).where(User.id == row.requested_by)" not in src
    assert "requester_names.get(row.requested_by" in src


def test_supplier_demand_alerts_tab_shows_city_and_expiry():
    """The tab must name the vendor's city (not their business name) and show the TTL."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "Frontend" / "src" / "app"
        / "components" / "supplier" / "SupplierDashboard.tsx"
    ).read_text(encoding="utf-8")

    assert 'looking for in {vendorCity || "your area"}' in src
    assert 'no pending demand alerts in {vendorCity || "your area"}' in src
    assert "setVendorCity((data.city || \"\").trim())" in src
    assert "const DEMAND_ALERT_TTL_HOURS = 48;" in src
    assert "demandAlertExpiryLabel" in src
    assert "/vendors/me/demand-alerts?page=1&page_size=100" in src


def test_supplier_dashboard_payment_pagination_handler_is_in_scope():
    """The Payments tab pagination calls fetchPayments from JSX, so it must be declared
    at component scope. It used to live inside the mount useEffect, which produced
    "Cannot find name 'fetchPayments'" plus a ReferenceError on Previous/Next."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "Frontend" / "src" / "app"
        / "components" / "supplier" / "SupplierDashboard.tsx"
    ).read_text(encoding="utf-8")

    # Two-space indent == component scope (four would mean inside the effect).
    assert "\n  const fetchPayments = async (page = 1) => {" in src
    assert "fetchPayments(payments.page - 1)" in src
    assert "fetchPayments(payments.page + 1)" in src
    assert "        const fetchPayments = async" not in src


def test_marketplace_catalogue_supports_browse_only_mode():
    """Vendor view must hide cart/ordering without forking the catalogue component."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "Frontend" / "src" / "app"
    catalog = (root / "components" / "MarketplaceCatalog.tsx").read_text(encoding="utf-8")
    assert "browseOnly" in catalog
    # Cart affordances are gated, and vendor mode never touches a cart at all.
    assert "{!browseOnly && (" in catalog
    assert "if (browseOnly) return;" in catalog
    # Filtering/search must be delegated to the API, not a single page of results.
    assert "dataService.getProducts(page, pageSize, vendorId || undefined, {" in catalog
    assert "search: debouncedSearch || undefined" in catalog
    assert "filteredProducts" not in catalog, "client-side filtering silently ignored later pages"

    detail = (root / "components" / "dashboard" / "ProductDetail.tsx").read_text(encoding="utf-8")
    assert "browseOnly" in detail

    page = (root / "components" / "supplier" / "SupplierMarketplace.tsx").read_text(encoding="utf-8")
    assert "Back to Dashboard" in page
    assert "browseOnly" in page


# ---- Vendor report PDF (watermarked, vendor-scoped) ----

def _report_pdf(rows: int = 3, **overrides):
    """Render a vendor report and return (pdf_bytes, fitz_doc)."""
    import fitz
    from app.services.vendor_report_service import build_vendor_report_pdf

    sections = [{
        "heading": "Orders",
        "columns": ["Order", "Status", "Your share"],
        "rows": [[f"BC-{i:03d}", "delivered", "NGN 1,000.00"] for i in range(rows)],
    }]
    pdf = build_vendor_report_pdf(
        vendor_name=overrides.pop("vendor_name", "Raintech Constructions"),
        period_label=overrides.pop("period_label", "Last 30 days"),
        summary_meta=overrides.pop("summary_meta", [("Delivered revenue", "NGN 0.00")]),
        sections=overrides.pop("sections", sections),
    )
    return pdf, fitz.open(stream=pdf, filetype="pdf")


def test_vendor_report_watermarks_every_page():
    """Every page must carry the BURNCOST watermark, not just the first."""
    pdf, doc = _report_pdf(rows=80)
    assert doc.page_count >= 3, "long tables must paginate"
    for page in doc:
        text = page.get_text()
        assert "BURNCOST" in text
        assert "Confidential" in text
    assert "Page 1 of" in doc[0].get_text()
    assert f"Page {doc.page_count} of {doc.page_count}" in doc[doc.page_count - 1].get_text()
    doc.close()


def test_vendor_report_rows_render_exactly_once():
    """Guards the shrink-and-duplicate bug: insert_htmlbox() paints overflowing content
    scaled down, so a naive 'retry on a new page' left an illegible copy behind."""
    pdf, doc = _report_pdf(rows=60)
    all_text = "".join(page.get_text() for page in doc)
    duplicates = [i for i in range(60) if all_text.count(f"BC-{i:03d}") != 1]
    assert duplicates == [], f"rows rendered more than once: {duplicates}"
    doc.close()


def test_vendor_report_money_is_ascii_safe():
    """The built-in PDF fonts have no naira glyph, so amounts must use an NGN prefix."""
    from app.services.vendor_report_service import money
    assert money(720231) == "NGN 720,231.00"
    assert money("122000") == "NGN 122,000.00"
    assert money(0) == "NGN 0.00"
    assert money(None) == "NGN 0.00"
    assert money("not-a-number") == "NGN 0.00"

    _, doc = _report_pdf(summary_meta=[("Vendor share", money(122000))])
    text = doc[0].get_text()
    assert "NGN 122,000.00" in text
    assert "\u20a6" not in text  # no naira sign anywhere
    doc.close()


def test_vendor_report_escapes_html_in_values():
    """Product/vendor text is user-controlled: it must be drawn literally, never parsed
    as markup (the renderer uses plain text drawing, so tags pass through as text)."""
    sections = [{
        "heading": "Products",
        "columns": ["Product", "Price"],
        "rows": [["<b>Cement</b> & Co", "NGN 5,000.00"]],
    }]
    _, doc = _report_pdf(sections=sections)
    text = "".join(page.get_text() for page in doc)
    assert "<b>Cement</b> & Co" in text
    doc.close()


def test_vendor_report_empty_section_shows_placeholder():
    sections = [{"heading": "Reviews", "columns": ["Date"], "rows": [], "empty": "No reviews yet."}]
    _, doc = _report_pdf(sections=sections)
    assert "No reviews yet." in "".join(page.get_text() for page in doc)
    doc.close()



def test_vendor_report_endpoint_is_vendor_scoped():
    """The report must be guarded by the vendor dependency, never the admin one."""
    import inspect
    from app.api.v1.endpoints import reports as reports_api

    paths = {getattr(route, "path", "") for route in reports_api.router.routes}
    assert "/vendor/summary" in paths

    src = inspect.getsource(reports_api.export_vendor_report)
    assert "get_current_verified_vendor" in src
    # No admin role guard anywhere in the handler.
    assert "require_roles" not in src
    assert "report_guard" not in src

    params = inspect.signature(reports_api.export_vendor_report).parameters
    assert "current_vendor" in params
    assert "current_user" not in params


def test_vendor_report_endpoint_reuses_dashboard_maths():
    """Single source of truth: the PDF must format figures via money() and pull the same
    aggregates the dashboard renders, so the document cannot disagree with the UI."""
    import inspect
    from app.api.v1.endpoints import reports as reports_api

    src = inspect.getsource(reports_api.export_vendor_report)
    for call in ("get_sales_analytics", "get_sales_comparison", "list_vendor_payments",
                 "get_orders_ui", "list_vendor_product_reviews", "get_on_time_delivery_rate"):
        assert call in src, f"{call} should be reused rather than re-derived"
    assert "build_vendor_report_pdf" in src
    assert "StreamingResponse" in src
    assert "application/pdf" in src
    assert "attachment;" in src
