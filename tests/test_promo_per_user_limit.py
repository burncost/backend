"""Per-user promo redemption cap (`max_uses_per_user`, 0 = unlimited).

Contract pinned here:
  * A signed-in buyer who has already redeemed the code their maximum number of
    times is told `valid=False` by `POST /promos/validate`.
  * A buyer still under the cap (or with no cap set) stays valid.
  * An anonymous caller cannot be checked per-buyer, so it falls back to the
    global verdict — the hard stop remains at checkout.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.endpoints.promos import validate_promo
from app.schemas.promo import PromoValidateRequest

BUYER = SimpleNamespace(id=uuid4())


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Scripted async session: each execute() pops the next scripted result.

    Call order inside `validate_promo` is: `get_by_code` (the promo), then — only
    when the per-user cap is checked — `count_user_uses` (an int).
    """

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt):
        if not self._results:
            raise AssertionError("unexpected extra db.execute() call")
        return _FakeResult(self._results.pop(0))


def _promo(**overrides):
    base = dict(
        id=uuid4(),
        code="SAVE10",
        is_active=True,
        max_uses=0,
        current_uses=0,
        max_uses_per_user=0,
        discount_amount=1000,
        min_order_amount=0,
        expires_at=None,
        scope_type="platform",
        scope_id=None,
        vendor_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _validate(results, user):
    return asyncio.run(
        validate_promo(PromoValidateRequest(code="SAVE10"), user=user, db=_FakeSession(results))
    )


def test_buyer_at_the_per_user_cap_is_blocked():
    res = _validate([_promo(max_uses_per_user=1), 1], BUYER)

    assert res.valid is False
    assert "maximum number of times" in res.message


def test_buyer_under_the_per_user_cap_is_allowed():
    res = _validate([_promo(max_uses_per_user=2), 1], BUYER)

    assert res.valid is True
    assert res.discount_amount == 1000


def test_unlimited_per_user_never_queries_usage():
    # Only `get_by_code` is scripted; a usage lookup would raise the assertion.
    res = _validate([_promo(max_uses_per_user=0)], BUYER)

    assert res.valid is True


def test_anonymous_caller_skips_the_per_user_check():
    # Cap is set, but with no signed-in user only `get_by_code` runs.
    res = _validate([_promo(max_uses_per_user=1)], None)

    assert res.valid is True
