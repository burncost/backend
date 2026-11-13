"""Creating a listing from a catalog SKU: image row + SKU collision handling.

Two behaviours shipped with the catalog-driven Add Product form are pinned here:

* ``image_url`` is not a Product column, so it must be popped before
  ``Product(**product_data)`` (an unexpected kwarg is a TypeError → HTTP 500)
  and stored as the product's primary ``ProductImage`` row instead.
* Catalog SKUs are shared by every vendor listing the same product, so a
  collision is resolved by suffixing (``-2``, ``-3``, ...) rather than rejected.

No database: the session and CRUD reads are stubbed, matching the other unit
tests in this suite.
"""
import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.product import ProductImage
from app.schemas.product import ProductCreate
from app.services import product_service as product_service_module
from app.services.product_service import ProductService


class _FakeSession:
    """Minimal AsyncSession stand-in: records what the service does."""

    def __init__(self):
        self.added = []
        self.committed = False
        self.refreshed = []
        self.flushed = 0

    async def execute(self, statement):
        return None

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        self.flushed += 1

    async def commit(self):
        self.committed = True

    async def refresh(self, instance):
        self.refreshed.append(instance)


def _payload(**overrides) -> ProductCreate:
    data = {
        "name": "Dangote General Purpose Portland-Limestone Cement",
        "description": "Bagged hydraulic binder for mixing concrete.",
        "short_description": "General purpose cement",
        "sku": "CEM-DANG-GPPLC",
        "base_price": Decimal("9500"),
        "quantity": 120,
        "unit_of_measure": "50 kg bag",
    }
    data.update(overrides)
    return ProductCreate(**data)


def _run_create(monkeypatch, payload, *, taken_skus=()):
    """Create with ``get_by_sku`` returning a clash for every SKU in ``taken_skus``."""
    db = _FakeSession()
    seen = []

    async def _get_by_sku(_db, *, sku):
        seen.append(sku)
        return SimpleNamespace(id=uuid.uuid4()) if sku in taken_skus else None

    monkeypatch.setattr(product_service_module.product_crud, "get_by_sku", _get_by_sku)

    service = ProductService(db)
    product = asyncio.run(service.create_product(payload, vendor_id=uuid.uuid4()))
    return product, db, seen


def _images(db) -> list:
    return [row for row in db.added if isinstance(row, ProductImage)]


def test_create_persists_catalog_image_as_primary_row(monkeypatch):
    product, db, _ = _run_create(
        monkeypatch,
        _payload(image_url="https://res.cloudinary.com/demo/cement.jpg"),
    )

    # Popped off the Product kwargs - the row itself is created.
    assert not hasattr(product, "image_url") or product.image_url is None
    images = _images(db)
    assert len(images) == 1
    image = images[0]
    assert image.image_url == "https://res.cloudinary.com/demo/cement.jpg"
    assert image.is_primary is True
    assert image.display_order == 0
    assert db.committed is True


def test_create_without_image_creates_no_image_row(monkeypatch):
    _, db, _ = _run_create(monkeypatch, _payload())

    assert _images(db) == []
    assert db.committed is True


def test_create_suffixes_a_taken_catalog_sku(monkeypatch):
    product, _, seen = _run_create(
        monkeypatch,
        _payload(),
        taken_skus={"CEM-DANG-GPPLC", "CEM-DANG-GPPLC-2"},
    )

    assert product.sku == "CEM-DANG-GPPLC-3"
    assert seen == ["CEM-DANG-GPPLC", "CEM-DANG-GPPLC-2", "CEM-DANG-GPPLC-3"]


def test_create_keeps_a_free_catalog_sku(monkeypatch):
    product, _, seen = _run_create(monkeypatch, _payload())

    assert product.sku == "CEM-DANG-GPPLC"
    assert seen == ["CEM-DANG-GPPLC"]


def test_create_gives_up_after_five_suffixes(monkeypatch):
    taken = {"CEM-DANG-GPPLC"} | {f"CEM-DANG-GPPLC-{n}" for n in range(2, 7)}

    with pytest.raises(ValueError) as exc:
        _run_create(monkeypatch, _payload(), taken_skus=taken)

    assert "unavailable" in str(exc.value)
