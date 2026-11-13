"""Vendor promo management: edit, cancel/reactivate and delete.

Contract pinned here for the vendor `/promos` endpoints the supplier dashboard
uses on its Active Promotions card:
  * Only the owner may edit or delete a code — another vendor's code is a 403, an
    unknown id a 404.
  * `PATCH` is a partial update: an omitted field keeps its stored value (the
    redemption counter and usage history must survive an edit), an explicit `null`
    clears a nullable column (expiry date, product/category target).
  * A rename colliding with an existing code is a 400 and changes nothing.
  * Re-targeting to a product must point at one of the caller's own products, and
    platform-wide scope stays admin-only.
  * Cancel (`is_active=false`) / reactivate flip that one column — checkout
    re-checks `is_active` (`orders.py`), so a cancelled code stops being redeemable
    while staying listed for reactivation.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.promos import update_promo, delete_promo
from app.schemas.promo import PromoCodeUpdate

VENDOR_ID = uuid4()
OTHER_VENDOR_ID = uuid4()
VENDOR = {"id": str(VENDOR_ID)}


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value


class _FakeSession:
    """Scripted async session: each execute() pops the next scripted result.

    Call order inside `update_promo` is: owner lookup (`get_by_id`), then — only
    when a *changed* code is sent — the uniqueness `get_by_code`, then — only for a
    `product` scope — the product lookup, and finally the `get_by_id` inside
    `promo_crud.update`.
    """

    def __init__(self, results):
        self._results = list(results)
        self.commits = 0
        self.refreshed = []
        self.deleted = None

    async def execute(self, stmt):
        if not self._results:
            raise AssertionError("unexpected extra db.execute() call")
        return _FakeResult(self._results.pop(0))

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def delete(self, obj):
        self.deleted = obj


def _promo(**overrides):
    base = dict(
        id=uuid4(),
        code="SAVE10",
        description=None,
        discount_amount=1000,
        max_uses=0,
        current_uses=3,
        max_uses_per_user=0,
        min_order_amount=0,
        is_active=True,
        vendor_id=VENDOR_ID,
        scope_type="vendor",
        scope_id=None,
        expires_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch(promo, results, payload, vendor=VENDOR):
    """Run `update_promo` with `promo` scripted as the owner lookup.

    `results` supplies any lookups in between (uniqueness `get_by_code`, product
    scope target); the write's own `get_by_id` is appended automatically, so the
    happy path reads `_patch(promo, [], {...})`.
    """
    db = _FakeSession([promo, *results, promo])
    return asyncio.run(
        update_promo(promo.id, PromoCodeUpdate(**payload), vendor=vendor, db=db)
    ), db


# ─── Ownership / existence ────────────────────────────────────────────────────

def test_unknown_code_is_404():
    db = _FakeSession([None])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_promo(uuid4(), PromoCodeUpdate(is_active=False), vendor=VENDOR, db=db))

    assert exc.value.status_code == 404
    assert db.commits == 0


def test_another_vendors_code_is_403():
    promo = _promo()
    db = _FakeSession([promo])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            update_promo(promo.id, PromoCodeUpdate(is_active=False), vendor={"id": str(OTHER_VENDOR_ID)}, db=db)
        )

    assert exc.value.status_code == 403
    assert promo.is_active is True  # untouched
    assert db.commits == 0


def test_platform_owned_code_is_not_vendor_editable():
    # vendor_id is NULL on an admin/platform code — never vendor-editable.
    promo = _promo(vendor_id=None, scope_type="platform")
    db = _FakeSession([promo])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_promo(promo.id, PromoCodeUpdate(discount_amount=250), vendor=VENDOR, db=db))

    assert exc.value.status_code == 403
    assert promo.discount_amount == 1000


# ─── Partial edit ─────────────────────────────────────────────────────────────

def test_patch_writes_only_the_fields_that_were_sent():
    promo = _promo()
    updated, db = _patch(promo, [], {"discount_amount": 500})

    assert updated.discount_amount == 500
    assert promo.code == "SAVE10"
    assert promo.is_active is True
    assert promo.current_uses == 3  # redemption history untouched
    assert db.commits == 1 and db.refreshed == [promo]


def test_code_is_upper_cased_and_expiry_can_be_cleared():
    promo = _promo(expires_at=None)
    # `None` for the uniqueness lookup: the new code is free.
    updated, _ = _patch(promo, [None], {"code": "save20", "expires_at": None})

    assert updated.code == "SAVE20"
    assert updated.expires_at is None


def test_rename_collision_is_rejected_without_touching_the_row():
    promo = _promo()
    other = _promo(id=uuid4(), code="OTHER")
    db = _FakeSession([promo, other])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_promo(promo.id, PromoCodeUpdate(code="other"), vendor=VENDOR, db=db))

    assert exc.value.status_code == 400
    assert "already exists" in exc.value.detail
    assert promo.code == "SAVE10"
    assert db.commits == 0


def test_unchanged_code_skips_the_uniqueness_lookup():
    # Only the owner lookup + the update's own lookup are scripted; a uniqueness
    # query would raise the fake's "unexpected extra db.execute()" assertion.
    promo = _promo()
    updated, _ = _patch(promo, [], {"code": "SAVE10", "max_uses": 25})

    assert updated.code == "SAVE10"
    assert updated.max_uses == 25


# ─── Scope re-targeting ───────────────────────────────────────────────────────

def test_product_scope_must_target_your_own_product():
    promo = _promo()
    foreign_product = SimpleNamespace(id=uuid4(), vendor_id=OTHER_VENDOR_ID)
    db = _FakeSession([promo, foreign_product])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            update_promo(
                promo.id,
                PromoCodeUpdate(scope_type="product", scope_id=foreign_product.id),
                vendor=VENDOR,
                db=db,
            )
        )

    assert exc.value.status_code == 403
    assert promo.scope_type == "vendor"
    assert db.commits == 0


def test_own_product_scope_is_accepted():
    promo = _promo()
    own_product = SimpleNamespace(id=uuid4(), vendor_id=VENDOR_ID)
    updated, _ = _patch(promo, [own_product], {"scope_type": "product", "scope_id": own_product.id})

    assert updated.scope_type == "product"
    assert updated.scope_id == own_product.id


def test_missing_target_id_is_400():
    promo = _promo()
    db = _FakeSession([promo])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            update_promo(promo.id, PromoCodeUpdate(scope_type="category", scope_id=None), vendor=VENDOR, db=db)
        )

    assert exc.value.status_code == 400
    assert db.commits == 0


def test_platform_scope_stays_admin_only():
    promo = _promo()
    db = _FakeSession([promo])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_promo(promo.id, PromoCodeUpdate(scope_type="platform"), vendor=VENDOR, db=db))

    assert exc.value.status_code == 403
    assert promo.scope_type == "vendor"


def test_switching_back_to_catalogue_scope_clears_the_stale_target():
    product_id = uuid4()
    promo = _promo(scope_type="product", scope_id=product_id)
    updated, _ = _patch(promo, [], {"scope_type": "vendor", "scope_id": None})

    assert updated.scope_type == "vendor"
    assert updated.scope_id is None


# ─── Cancel / reactivate ──────────────────────────────────────────────────────

def test_cancel_then_reactivate_flips_only_is_active():
    promo = _promo()
    cancelled, _ = _patch(promo, [], {"is_active": False})

    assert cancelled.is_active is False
    assert cancelled.code == "SAVE10"
    assert cancelled.discount_amount == 1000
    assert cancelled.current_uses == 3

    reactivated, _ = _patch(promo, [], {"is_active": True})

    assert reactivated.is_active is True
    assert reactivated is promo  # same row, switched back on


# ─── Delete ───────────────────────────────────────────────────────────────────

def test_delete_unknown_code_is_404():
    db = _FakeSession([None])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(delete_promo(uuid4(), vendor=VENDOR, db=db))

    assert exc.value.status_code == 404
    assert db.deleted is None


def test_delete_someone_elses_code_is_403_and_keeps_the_row():
    promo = _promo()
    db = _FakeSession([promo])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(delete_promo(promo.id, vendor={"id": str(OTHER_VENDOR_ID)}, db=db))

    assert exc.value.status_code == 403
    assert db.deleted is None
    assert db.commits == 0


def test_owner_can_delete_their_code():
    promo = _promo()
    # Two lookups: the endpoint's ownership check + `promo_crud.delete`'s own.
    db = _FakeSession([promo, promo])
    assert asyncio.run(delete_promo(promo.id, vendor=VENDOR, db=db)) is None

    assert db.deleted is promo
    assert db.commits == 1


