"""Editing a product keeps sku / unit_of_measure / minimum_order_quantity.

Two regressions shipped with the vendor edit form and are pinned here:

* ``ProductUpdate`` did not declare the three listing fields, so Pydantic dropped
  them and the PUT persisted nothing (vendor hit save, nothing changed).
* ``specifications`` is declared, but the endpoint ``setattr``-ed the list of dicts
  straight onto the SQLAlchemy relationship, which raises on flush (500 for any
  edit that included spec rows). Specs now replace their child rows instead.

No database: the session and CRUD reads are stubbed, matching the other unit
tests in this suite.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import products as products_api
from app.models.product import ProductSpecification
from app.schemas.product import ProductUpdate
from app.services import product_service as product_service_module
from app.services.product_service import ProductService


# ── Schema: the three fields must survive validation ────────────────────

def test_product_update_accepts_listing_fields():
    payload = ProductUpdate(
        sku="BC-CEM-DANG-0099",
        unit_of_measure="bag",
        minimum_order_quantity=25,
    )

    assert payload.model_dump(exclude_unset=True) == {
        "sku": "BC-CEM-DANG-0099",
        "unit_of_measure": "bag",
        "minimum_order_quantity": 25,
    }


def test_product_update_rejects_non_positive_moq():
    with pytest.raises(ValueError):
        ProductUpdate(minimum_order_quantity=0)


def test_product_update_leaves_unset_fields_alone():
    # exclude_unset is what lets a partial edit leave stock fields untouched.
    dumped = ProductUpdate(name="Portland Cement 50kg").model_dump(exclude_unset=True)

    assert set(dumped) == {"name"}


# ── Endpoint: writes reach the product, specs go to the child table ─────

class _FakeSession:
    """Minimal AsyncSession stand-in: records what the handler does."""

    def __init__(self):
        self.added = []
        self.executed = []
        self.committed = False
        self.refreshed = []
        self.rolled_back = False

    async def execute(self, statement):
        self.executed.append(statement)

    def add(self, instance):
        self.added.append(instance)

    async def commit(self):
        self.committed = True

    async def refresh(self, instance):
        self.refreshed.append(instance)

    async def rollback(self):
        self.rolled_back = True


def _product(sku: str = "BC-CEM-DANG-0001") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        sku=sku,
        name="Dangote Cement 50kg",
        unit_of_measure="piece",
        minimum_order_quantity=1,
        specifications=[],
    )


def _call_update(monkeypatch, payload, *, product=None, sku_clash=None):
    product = product or _product()
    db = _FakeSession()
    seen = {}

    async def _get(_db, *, id):
        return product

    async def _get_by_sku(_db, *, sku):
        seen["sku_checked"] = sku
        return sku_clash

    monkeypatch.setattr(products_api.product_crud, "get", _get)
    monkeypatch.setattr(products_api.product_crud, "get_by_sku", _get_by_sku)

    result = asyncio.run(
        products_api.update_product(
            product_id=product.id,
            product_in=payload,
            current_vendor={"id": str(product.vendor_id)},
            db=db,
        )
    )
    return result, product, db, seen



def test_update_persists_sku_unit_and_moq(monkeypatch):
    result, product, db, seen = _call_update(
        monkeypatch,
        ProductUpdate(
            sku="BC-CEM-DANG-0099",
            unit_of_measure="bag",
            minimum_order_quantity=25,
        ),
    )

    assert result.status_code == 200
    assert (product.sku, product.unit_of_measure, product.minimum_order_quantity) == (
        "BC-CEM-DANG-0099",
        "bag",
        25,
    )
    assert seen["sku_checked"] == "BC-CEM-DANG-0099"
    assert db.committed is True


def test_update_skips_sku_lookup_when_unchanged(monkeypatch):
    product = _product(sku="BC-CEM-DANG-0001")

    result, _, _, seen = _call_update(
        monkeypatch, ProductUpdate(sku="BC-CEM-DANG-0001"), product=product
    )

    assert result.status_code == 200
    assert "sku_checked" not in seen


def test_update_rejects_duplicate_sku(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _call_update(
            monkeypatch,
            ProductUpdate(sku="BC-TAKEN-0001"),
            sku_clash=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc.value.status_code == 400
    assert "already exists" in exc.value.detail


def test_update_replaces_specifications_instead_of_setattr(monkeypatch):
    # Specs-only payload: no scalar fields, so this also proves the "No fields
    # provided for update" guard accounts for a specifications-only edit.
    result, product, db, _ = _call_update(
        monkeypatch,
        ProductUpdate(
            specifications=[
                {"spec_name": "Grade", "spec_value": "42.5N"},
                {"spec_name": "Blank", "spec_value": "   "},
            ]
        ),
    )

    assert result.status_code == 200
    # Never assigned onto the relationship (that is what used to 500 on flush).
    assert product.specifications == []
    # Old rows deleted by id, not by loading the collection (async lazy-load safe).
    assert len(db.executed) == 1
    assert "DELETE FROM product_specifications" in str(db.executed[0])
    # Blank rows skipped, the same rule create_product applies.
    assert len(db.added) == 1
    spec = db.added[0]
    assert isinstance(spec, ProductSpecification)
    assert (spec.product_id, spec.spec_name, spec.spec_value, spec.display_order) == (
        product.id,
        "Grade",
        "42.5N",
        0,
    )


def test_update_with_empty_specifications_clears_them(monkeypatch):
    result, _, db, _ = _call_update(monkeypatch, ProductUpdate(specifications=[]))

    assert result.status_code == 200
    assert len(db.executed) == 1
    assert db.added == []


# ── Service layer: ProductService.update_product mirrors the endpoint ───

def _call_service_update(monkeypatch, payload, *, product=None, sku_clash=None):
    """Same stubs as the endpoint helper, applied to the service module.

    Both modules import the same ``app.crud.product.product`` singleton, so the
    patch has to land on the CRUD object rather than on either importer.
    """
    product = product or _product()
    db = _FakeSession()

    async def _get(_db, *, id):
        return product

    async def _get_by_sku(_db, *, sku):
        return sku_clash

    monkeypatch.setattr(product_service_module.product_crud, "get", _get)
    monkeypatch.setattr(product_service_module.product_crud, "get_by_sku", _get_by_sku)

    result = asyncio.run(
        ProductService(db).update_product(product_id=product.id, product_in=payload)
    )
    return result, product, db


def test_service_update_replaces_specifications(monkeypatch):
    result, product, db = _call_service_update(
        monkeypatch,
        ProductUpdate(
            specifications=[
                {"spec_name": "Grade", "spec_value": "42.5N"},
                {"spec_name": "Blank", "spec_value": "   "},
            ]
        ),
    )

    assert result is product
    assert product.specifications == []  # never assigned onto the relationship
    assert len(db.executed) == 1
    assert "DELETE FROM product_specifications" in str(db.executed[0])
    assert len(db.added) == 1
    spec = db.added[0]
    assert isinstance(spec, ProductSpecification)
    assert (spec.product_id, spec.spec_name, spec.spec_value, spec.display_order) == (
        product.id,
        "Grade",
        "42.5N",
        0,
    )


def test_service_update_rejects_duplicate_sku(monkeypatch):
    with pytest.raises(ValueError) as exc:
        _call_service_update(
            monkeypatch,
            ProductUpdate(sku="BC-TAKEN-0001"),
            sku_clash=SimpleNamespace(id=uuid.uuid4()),
        )

    # ValueError, not HTTPException: the service layer stays transport agnostic.
    assert "already exists" in str(exc.value)


def test_service_update_returns_none_for_missing_product(monkeypatch):
    async def _get(_db, *, id):
        return None

    monkeypatch.setattr(product_service_module.product_crud, "get", _get)

    db = _FakeSession()
    result = asyncio.run(
        ProductService(db).update_product(
            product_id=uuid.uuid4(), product_in=ProductUpdate(name="Portland Cement 50kg")
        )
    )

    assert result is None
    assert db.committed is False
