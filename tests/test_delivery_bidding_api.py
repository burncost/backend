"""Phase 3 delivery-bidding API contract (no database, no network).

Pins the public surface the buyer/driver/vendor clients will call plus the two
gates that keep money safe:
  * every endpoint the UI depends on stays registered under /delivery,
  * a bid price can never be zero/negative (server-side, not just the form),
  * a vendor can never quote or assign another vendor's job,
  * delivery payments use the SHP- prefix the webhook/verify routes dispatch on,
  * job payloads expose the shipping-paid state the pickup gate depends on.
"""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints import delivery_bidding as api
from app.api.v1.endpoints.deliveries import _job_dict
from app.models.delivery import DeliveryJob
from app.services.shipping_payment_service import _reference

_EXPECTED_ROUTES = {
    (("GET",), "/orders/{order_ref}/arrangement"),
    (("POST",), "/bids/{bid_id}/accept"),
    (("POST",), "/bids/{bid_id}/reject"),
    (("POST",), "/jobs/{job_id}/decline-vendor"),
    (("POST",), "/jobs/{job_id}/extend"),
    (("POST",), "/orders/{order_ref}/shipping/pay"),
    (("POST",), "/shipping/confirm"),
    (("GET",), "/shipping/verify"),
    (("POST",), "/jobs/{job_id}/quote"),
    (("POST",), "/jobs/{job_id}/assign"),
    (("GET",), "/bids/open"),
    (("GET",), "/bids/mine"),
    (("POST",), "/jobs/{job_id}/bids"),
    (("DELETE",), "/bids/{bid_id}"),
}


def _routes():
    return {(tuple(sorted(r.methods)), r.path) for r in api.router.routes}


def test_delivery_arrangement_endpoints_are_registered():
    assert _EXPECTED_ROUTES <= _routes()


def test_shipping_payments_use_the_shp_prefix_routed_by_webhooks():
    reference = _reference()
    assert reference.startswith("SHP-")
    # The webhooks/verify only settle invoices they can recognise by prefix.
    assert reference.startswith("SHP-") and not reference.startswith("BURNCOST-")


def test_driver_bid_amount_must_be_positive():
    with pytest.raises(ValidationError):
        api.BidCreate(amount=0)
    with pytest.raises(ValidationError):
        api.BidCreate(amount=-500)


def test_vendor_quote_amount_must_be_positive():
    with pytest.raises(ValidationError):
        api.QuoteCreate(amount=0)


def test_foreign_vendor_cannot_touch_a_job():
    job = DeliveryJob(id=uuid4(), vendor_id=uuid4())
    vendor = type("V", (), {"id": uuid4()})()
    with pytest.raises(HTTPException) as exc:
        api._require_vendor_job(job, vendor)
    assert exc.value.status_code == 403


def test_owning_vendor_passes_the_ownership_gate():
    vendor_id = uuid4()
    job = DeliveryJob(id=uuid4(), vendor_id=vendor_id)
    api._require_vendor_job(job, type("V", (), {"id": vendor_id})())


def test_job_payload_exposes_the_shipping_paid_gate():
    job = DeliveryJob(
        id=uuid4(),
        job_number="DLV-TEST-1",
        order_id=uuid4(),
        vendor_id=uuid4(),
        assignment_type="bid",
        status="available",
        shipping_paid=False,
    )
    payload = _job_dict(job, order_number="BC-20261113-TEST")
    assert payload["shipping_paid"] is False
    assert payload["assignment_type"] == "bid"
    assert "bidding_closes_at" in payload
    assert "shipping_invoice_id" in payload


def test_legacy_offer_dispatch_is_retired_for_bid_jobs():
    """The first-come `offer` mode is gone: a paid order is bid out instead.

    `POST /delivery/jobs` can only open/re-open the auto-created bid jobs (`bid`)
    or hand the job to a fleet driver (`direct`). Only a direct assignment states
    the fee up front - a bid job takes its fee from the winning bid / quote, so a
    zero fee there can never release a driver for money nobody owes.
    """
    from app.api.v1.endpoints.deliveries import DispatchCreate, router, vendor_create_dispatch
    import inspect

    # The retired mode no longer validates.
    with pytest.raises(ValidationError):
        DispatchCreate(order_id="BC-1", assignment_type="offer", delivery_fee=2500)
    with pytest.raises(ValidationError):
        DispatchCreate(order_id="BC-1", assignment_type="direct", delivery_fee=0)

    assert DispatchCreate(order_id="BC-1", assignment_type="bid").delivery_fee is None
    assert DispatchCreate(order_id="BC-1", assignment_type="bid", delivery_fee=2500).delivery_fee == 2500
    assert DispatchCreate(order_id="BC-1", assignment_type="direct", delivery_fee=2500).delivery_fee == 2500

    # The driver-side offer routes were removed with the mode.
    paths = {r.path for r in router.routes}
    assert "/jobs" in paths
    assert "/offers" not in paths
    assert "/offers/{job_id}/accept" not in paths

    source = inspect.getsource(vendor_create_dispatch)
    assert "create_bid_jobs_for_order" in source
    assert "delivery_fee is required" in source
    assert "shipping_paid=False" in source
    # The fee must be validated by the shared money rules, not trusted as sent.
    assert "validate_delivery_fee" in source
