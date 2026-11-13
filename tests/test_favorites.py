"""
Product favourites endpoints — add/remove idempotency and auth guard.

Guests keep favourites in localStorage and replay to POST /favorites/{id} on
sign-in, so `add_favorite` must be idempotent (merging must never 500/duplicate).
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.favorites import add_favorite, remove_favorite


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def rowcount(self):
        return 1


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        if not self._results:
            raise AssertionError("unexpected extra db.execute() call")
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _user():
    return SimpleNamespace(id=uuid4())


def test_add_favorite_creates_row_for_new_product():
    product = SimpleNamespace(id=uuid4())
    db = _FakeSession([product, None])  # product found, no existing favourite

    result = asyncio.run(add_favorite(product_id=product.id, current_user=_user(), db=db))

    assert result == {"success": True, "favorited": True, "already": False}
    assert len(db.added) == 1
    assert db.committed is True


def test_add_favorite_is_idempotent_when_already_saved():
    product = SimpleNamespace(id=uuid4())
    existing = SimpleNamespace(id=uuid4())
    db = _FakeSession([product, existing])

    result = asyncio.run(add_favorite(product_id=product.id, current_user=_user(), db=db))

    assert result == {"success": True, "favorited": True, "already": True}
    assert db.added == []


def test_add_favorite_404s_for_unknown_product():
    db = _FakeSession([None])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(add_favorite(product_id=uuid4(), current_user=_user(), db=db))
    assert caught.value.status_code == 404


def test_remove_favorite_is_idempotent():
    db = _FakeSession([None])  # delete() ignores the result set
    result = asyncio.run(remove_favorite(product_id=uuid4(), current_user=_user(), db=db))
    assert result == {"success": True, "favorited": False}
    assert db.committed is True
