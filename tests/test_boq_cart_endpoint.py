"""`/boqs/cart-preview` and `GET /boqs/{id}` for bills uploaded for verification.

An uploaded bill lives in `boq_verifications`, not in the generated-BOQ
collection, and it carries no `boqData` wrapper: `elements[].items` sits at the
top level. These tests pin the Phase 0 contract - the cart preview now falls back
to that collection so the upload can be ordered, but only by the account that
uploaded it (guest uploads are never stored, so every row has an owner).
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api.v1.endpoints import boqs
from app.schemas.boq import BOQCartRequest


def _run(coro):
    return asyncio.run(coro)


def _verification_doc(uploaded_by, **over):
    doc = {
        "_id": ObjectId(),
        "record_type": "verification",
        "filename": "contractor-bill.xlsx",
        "uploadedBy": uploaded_by,
        "city": "Abuja",
        "items": [],
        "elements": [
            {
                "element_name": "Substructure",
                "items": [
                    {
                        "item_code": "CON-001",
                        "description": "Ordinary Portland cement",
                        "unit": "bag",
                        "quantity": 20,
                        "rate": 9000,
                    }
                ],
            }
        ],
        "total_quoted": 180000,
        "total_market": 160000,
    }
    doc.update(over)
    return doc


def _generated_doc(created_by, **over):
    doc = {
        "_id": ObjectId(),
        "createdBy": created_by,
        "boqData": {"elements": [{"element_name": "Substructure", "items": []}]},
    }
    doc.update(over)
    return doc


class _Collection:
    def __init__(self, doc):
        self.doc = doc
        self.queries = []

    async def find_one(self, query, *args, **kwargs):
        self.queries.append(query)
        if self.doc and query.get("_id") == self.doc["_id"]:
            return self.doc
        return None


class _FakeDB:
    """Mongo stand-in exposing only the `boq_verifications` collection."""

    def __init__(self, verification=None):
        self.verification = _Collection(verification)

    def __getitem__(self, name):
        assert name == "boq_verifications"
        return self.verification


class _ExplodingDB:
    """Proves the inline-payload path never touches Mongo."""

    def __getitem__(self, name):  # pragma: no cover - only hit on regression
        raise AssertionError(f"unexpected collection access: {name}")


class _Repo:
    """BOQRepository stand-in; `docs` maps boq_id -> generated document."""

    def __init__(self, docs=None):
        self.docs = docs or {}

    def __call__(self, db):
        return self

    async def get_by_id(self, boq_id):
        return self.docs.get(boq_id)


class _CartService:
    """BOQCartService stand-in that records the payload it was asked to resolve."""

    calls = []

    def __init__(self, db):
        self.db = db

    async def resolve(self, boq, city="", limit_offers=3):
        _CartService.calls.append({"boq": boq, "city": city, "limit_offers": limit_offers})
        return {
            "lines": [],
            "unmatched": [],
            "skipped": [],
            "subtotal": 0.0,
            "notes": [],
        }


@pytest.fixture(autouse=True)
def _patch_service(monkeypatch):
    _CartService.calls.clear()
    monkeypatch.setattr(boqs, "BOQCartService", _CartService)



# ── Cart preview: uploaded-for-verification bills ────────────────────────────


def test_owned_verification_resolves_through_cart_preview(monkeypatch):
    user = _user()
    doc = _verification_doc(user.id)
    monkeypatch.setattr(boqs, "BOQRepository", _Repo())

    result = _run(
        boqs.preview_boq_cart(
            BOQCartRequest(boq_id=str(doc["_id"])),
            current_user=user,
            db=_FakeDB(doc),
            pg_db=MagicMock(),
        )
    )

    assert result["lines"] == []
    assert len(_CartService.calls) == 1
    resolved = _CartService.calls[0]["boq"]
    # The raw verification document is resolved in place - no boqData unwrapping.
    assert resolved["elements"][0]["items"][0]["item_code"] == "CON-001"
    assert resolved["elements"][0]["items"][0]["quantity"] == 20


def test_verification_of_another_account_is_forbidden(monkeypatch):
    doc = _verification_doc(str(uuid4()))
    monkeypatch.setattr(boqs, "BOQRepository", _Repo())

    with pytest.raises(HTTPException) as exc:
        _run(
            boqs.preview_boq_cart(
                BOQCartRequest(boq_id=str(doc["_id"])),
                current_user=_user(),
                db=_FakeDB(doc),
                pg_db=MagicMock(),
            )
        )

    assert exc.value.status_code == 403
    assert _CartService.calls == []


def test_guest_cannot_order_a_stored_verification(monkeypatch):
    doc = _verification_doc(str(uuid4()))
    monkeypatch.setattr(boqs, "BOQRepository", _Repo())

    with pytest.raises(HTTPException) as exc:
        _run(
            boqs.preview_boq_cart(
                BOQCartRequest(boq_id=str(doc["_id"])),
                current_user=None,
                db=_FakeDB(doc),
                pg_db=MagicMock(),
            )
        )

    assert exc.value.status_code == 401
    assert _CartService.calls == []


def test_unknown_boq_id_is_not_found(monkeypatch):
    monkeypatch.setattr(boqs, "BOQRepository", _Repo())

    with pytest.raises(HTTPException) as exc:
        _run(
            boqs.preview_boq_cart(
                BOQCartRequest(boq_id=str(ObjectId())),
                current_user=_user(),
                db=_FakeDB(None),
                pg_db=MagicMock(),
            )
        )

    assert exc.value.status_code == 404


def test_foreign_generated_boq_is_forbidden(monkeypatch):
    doc = _generated_doc(str(uuid4()))
    monkeypatch.setattr(boqs, "BOQRepository", _Repo({str(doc["_id"]): doc}))

    with pytest.raises(HTTPException) as exc:
        _run(
            boqs.preview_boq_cart(
                BOQCartRequest(boq_id=str(doc["_id"])),
                current_user=_user(),
                db=_FakeDB(None),
                pg_db=MagicMock(),
            )
        )

    assert exc.value.status_code == 403


def test_inline_payload_still_resolves_without_touching_mongo():
    payload = {"elements": [{"element_name": "Substructure", "items": []}]}

    result = _run(
        boqs.preview_boq_cart(
            BOQCartRequest(boq=payload),
            current_user=None,
            db=_ExplodingDB(),
            pg_db=MagicMock(),
        )
    )

    assert result["lines"] == []
    assert _CartService.calls[0]["boq"] == payload


def test_generated_boq_is_unwrapped_from_boq_data(monkeypatch):
    user = _user()
    doc = _generated_doc(user.id)
    monkeypatch.setattr(boqs, "BOQRepository", _Repo({str(doc["_id"]): doc}))

    _run(
        boqs.preview_boq_cart(
            BOQCartRequest(boq_id=str(doc["_id"])),
            current_user=user,
            db=_FakeDB(None),
            pg_db=MagicMock(),
        )
    )

    assert _CartService.calls[0]["boq"] == doc["boqData"]


# ── GET /boqs/{id}: uploads stay private to their uploader ───────────────────


def test_get_boq_returns_owned_verification(monkeypatch):
    user = _user()
    doc = _verification_doc(user.id)
    monkeypatch.setattr(boqs, "BOQRepository", _Repo())

    result = _run(boqs.get_boq(str(doc["_id"]), current_user=user, db=_FakeDB(doc)))

    assert result["source"] == "verification"
    assert result["title"] == "contractor-bill.xlsx"
    assert result["_id"] == str(doc["_id"])


def test_get_boq_hides_another_accounts_verification(monkeypatch):
    doc = _verification_doc(str(uuid4()))
    monkeypatch.setattr(boqs, "BOQRepository", _Repo())

    with pytest.raises(HTTPException) as exc:
        _run(boqs.get_boq(str(doc["_id"]), current_user=_user(), db=_FakeDB(doc)))

    assert exc.value.status_code == 403


def _user(user_id=None):
    return SimpleNamespace(id=user_id or str(uuid4()))
