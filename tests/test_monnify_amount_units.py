"""Monnify must be initialized in Naira, never kobo.

Regression: the Monnify branch sent ``amount * 100`` (a Paystack habit), so the
NGN 64,297.03 order BC-20261016-LKS2I5 rendered as "Amount to pay
NGN 6,429,703.00" on the hosted checkout. Monnify's init-transaction endpoint
takes the major unit (Naira, 2dp); Paystack still takes kobo.

Pure unit tests: the HTTP client is faked, no network and no database.
"""

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services.payment_service import PaymentService

ORDER_TOTAL = 64297.03  # what BC-20261016-LKS2I5 should have been charged
REFERENCE = "BURNCOST-BC-20261016-LKS2I5-20261016120000"


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Records every outbound POST so the test can assert the amount unit."""

    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None, headers=None, **kwargs):
        _FakeAsyncClient.calls.append({"url": url, "json": json or {}})
        if url.endswith("/api/v1/auth/login"):
            return _FakeResponse({"responseBody": {"accessToken": "test-token"}})
        if "monnify" in url:
            return _FakeResponse({
                "responseBody": {
                    "checkoutUrl": "https://sandbox.monnify.com/checkout/TEST",
                    "transactionReference": "MNFY-TEST",
                }
            })
        return _FakeResponse({
            "status": True,
            "data": {
                "authorization_url": "https://checkout.paystack.com/test",
                "access_code": "test-access-code",
                "reference": REFERENCE,
            },
        })


@pytest.fixture(autouse=True)
def _gateway_config(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(settings, "DEBUG", False, raising=False)
    monkeypatch.setattr(settings, "MOCK_PAYMENT_GATEWAY", False, raising=False)
    monkeypatch.setattr(settings, "MONNIFY_PUBLIC_KEY", "pk_test", raising=False)
    monkeypatch.setattr(settings, "MONNIFY_SECRET_KEY", "sk_test", raising=False)
    monkeypatch.setattr(settings, "MONNIFY_CONTRACT_CODE", "1234567890", raising=False)
    monkeypatch.setattr(settings, "MONNIFY_BASE_URL", "https://sandbox.monnify.com", raising=False)
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", "sk_test_paystack", raising=False)
    monkeypatch.setattr(settings, "PAYSTACK_PUBLIC_KEY", "pk_test_paystack", raising=False)
    # Keep Flutterwave out of the default-provider path so `provider=...` is unambiguous.
    monkeypatch.setattr(settings, "FLUTTERWAVE_SECRET_KEY", None, raising=False)
    yield


def _post_to(url_suffix):
    matching = [c for c in _FakeAsyncClient.calls if c["url"].endswith(url_suffix)]
    assert matching, f"no POST to {url_suffix}; calls={[c['url'] for c in _FakeAsyncClient.calls]}"
    return matching[-1]


def test_monnify_init_sends_the_naira_total():
    result = asyncio.run(PaymentService().initialize_payment(
        amount=ORDER_TOTAL,
        email="buyer@example.com",
        reference=REFERENCE,
        provider="monnify",
    ))

    assert result["success"] is True
    amount = _post_to("/api/v1/merchant/transactions/init-transaction")["json"]["amount"]
    assert amount == pytest.approx(64297.03)
    # The exact failure: 100x the order total.
    assert amount != 6429703
    assert amount != pytest.approx(6429703.0)


def test_monnify_keeps_the_kobo_fraction_of_the_total():
    # Order totals arrive as Decimal; the 2dp fraction must survive the cast.
    asyncio.run(PaymentService().initialize_payment(
        amount=ORDER_TOTAL,
        email="buyer@example.com",
        reference=REFERENCE,
        provider="monnify",
    ))

    amount = _post_to("init-transaction")["json"]["amount"]
    assert round(amount, 2) == amount
    assert amount == pytest.approx(64297.03, abs=1e-9)


def test_paystack_still_receives_kobo():
    # Guarantee the Monnify fix never leaks into the kobo gateway.
    asyncio.run(PaymentService().initialize_payment(
        amount=ORDER_TOTAL,
        email="buyer@example.com",
        reference=REFERENCE,
        provider="paystack",
    ))

    amount = _post_to("/transaction/initialize")["json"]["amount"]
    assert amount == 6429703
