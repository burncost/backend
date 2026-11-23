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
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictException
from app.models.brand import Brand
from app.models.product import ProductImage, ProductSpecification
from app.schemas.product import ProductCreate, ProductResponse
from app.services import product_service as product_service_module
from app.services.product_service import ProductService


class _FakeResult:
    """Stand-in for a SQLAlchemy Result with a single scalar row."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Minimal AsyncSession stand-in: records what the service does."""

    def __init__(self):
        self.added = []
        self.committed = False
        self.refreshed = []
        self.flushed = 0
        self.rolled_back = 0
        self.savepoints = 0

    async def execute(self, statement):
        return None

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        self.flushed += 1
        # Mimic the column defaults the database would fill in.
        for instance in self.added:
            if getattr(instance, "id", None) is None:
                instance.id = uuid.uuid4()

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back += 1

    async def refresh(self, instance):
        self.refreshed.append(instance)

    def begin_nested(self):
        session = self

        class _Savepoint:
            async def __aenter__(self):
                session.savepoints += 1
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False  # let an IntegrityError reach resolve_brand_id

        return _Savepoint()


class _FailingSession(_FakeSession):
    """Session whose flush/commit hits a unique constraint."""

    def __init__(self, *, fail_flush: bool = False, fail_commit: bool = False):
        super().__init__()
        self.fail_flush = fail_flush
        self.fail_commit = fail_commit

    async def flush(self):
        if self.fail_flush:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        await super().flush()

    async def commit(self):
        if self.fail_commit:
            raise IntegrityError("COMMIT", {}, Exception("duplicate key"))
        await super().commit()


class _BrandRaceSession(_FakeSession):
    """A concurrent request inserts the same brand between lookup and insert."""

    def __init__(self):
        super().__init__()
        self.calls = 0
        self.winner = SimpleNamespace(id=uuid.uuid4())

    async def execute(self, statement):
        self.calls += 1
        # 1: name lookup (miss), 2: slug lookup (miss), 3: name lookup after
        # the savepoint failure (the row the other request committed).
        return _FakeResult(self.winner if self.calls >= 3 else None)

    async def flush(self):
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))


class _SlugTakenSession(_FakeSession):
    """The slug normalises onto a brand with a different display name."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def execute(self, statement):
        self.calls += 1
        # 1: name lookup (miss); 2: slug lookup (taken).
        return _FakeResult(uuid.uuid4() if self.calls == 2 else None)


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


def _run_create(monkeypatch, payload, *, taken_skus=(), session=None):
    """Create with ``get_by_sku`` returning a clash for every SKU in ``taken_skus``."""
    db = session if session is not None else _FakeSession()
    seen = []

    async def _get_by_sku(_db, *, sku):
        seen.append(sku)
        return SimpleNamespace(id=uuid.uuid4()) if sku in taken_skus else None

    monkeypatch.setattr(product_service_module.product_crud, "get_by_sku", _get_by_sku)

    service = ProductService(db)
    product = asyncio.run(service.create_product(payload, vendor_id=uuid.uuid4()))
    return product, db, seen


def test_create_persists_specifications(monkeypatch):
    product, db, _ = _run_create(
        monkeypatch,
        _payload(specifications=[
            {"spec_name": "Grade", "spec_value": " 42.5N "},
            {"spec_name": "Bag Weight", "spec_value": "50 kg", "display_order": 1},
        ]),
    )

    assert [
        (s.spec_name, s.spec_value, s.display_order) for s in _specs(db)
    ] == [("Grade", "42.5N", 0), ("Bag Weight", "50 kg", 1)]
    assert all(s.product_id == product.id for s in _specs(db))
    assert db.committed


def test_create_skips_blank_specification_rows(monkeypatch):
    _, db, _ = _run_create(
        monkeypatch,
        _payload(specifications=[
            {"spec_name": "Grade", "spec_value": "42.5N"},
            {"spec_name": "Empty", "spec_value": "   "},
        ]),
    )

    assert [s.spec_name for s in _specs(db)] == ["Grade"]


def test_create_without_specifications_writes_no_spec_rows(monkeypatch):
    _, db, _ = _run_create(monkeypatch, _payload())

    assert _specs(db) == []
    assert db.committed


def test_product_response_serialises_specification_rows():
    """``my-products`` and the detail GET build the payload from
    ``**product.__dict__``, so saved specs reach the response as ORM objects.
    They must survive ``ProductResponse`` validation or the vendor edit screen
    receives an empty list and cannot prefill them."""
    spec = SimpleNamespace(
        id=uuid.uuid4(), spec_name="Grade", spec_value="42.5N", display_order=0
    )
    now = datetime.now(timezone.utc)

    response = ProductResponse.model_validate({
        "id": uuid.uuid4(),
        "vendor_id": uuid.uuid4(),
        "slug": "dangote-general-purpose-cement",
        "status": "active",
        "category": "Cement",
        "name": "Dangote General Purpose Portland-Limestone Cement",
        "sku": "CEM-DANG-GPPLC",
        "base_price": Decimal("9500"),
        "rating": Decimal("0"),
        "view_count": 0,
        "sales_count": 0,
        "is_featured": True,
        "is_verified": True,
        "created_at": now,
        "updated_at": now,
        "specifications": [spec],
    })

    assert [(s.spec_name, s.spec_value, s.display_order) for s in response.specifications] == [
        ("Grade", "42.5N", 0)
    ]


def test_product_response_defaults_to_no_specifications():
    now = datetime.now(timezone.utc)

    response = ProductResponse.model_validate({
        "id": uuid.uuid4(),
        "vendor_id": uuid.uuid4(),
        "slug": "dangote-general-purpose-cement",
        "status": "active",
        "category": "Cement",
        "name": "Dangote General Purpose Portland-Limestone Cement",
        "sku": "CEM-DANG-GPPLC",
        "base_price": Decimal("9500"),
        "rating": Decimal("0"),
        "view_count": 0,
        "sales_count": 0,
        "is_featured": True,
        "is_verified": True,
        "created_at": now,
        "updated_at": now,
    })

    assert response.specifications == []


def test_create_maps_a_lost_sku_race_to_conflict(monkeypatch):
    db = _FailingSession(fail_flush=True)

    with pytest.raises(ConflictException) as exc:
        _run_create(monkeypatch, _payload(), session=db)

    assert "SKU 'CEM-DANG-GPPLC'" in str(exc.value)
    assert db.rolled_back == 1


def test_create_maps_a_commit_conflict_to_conflict(monkeypatch):
    db = _FailingSession(fail_commit=True)

    with pytest.raises(ConflictException) as exc:
        _run_create(monkeypatch, _payload(), session=db)

    assert "conflicts" in str(exc.value)
    assert db.rolled_back == 1


def test_resolve_brand_id_reuses_a_concurrently_created_brand():
    db = _BrandRaceSession()

    brand_id = asyncio.run(ProductService(db).resolve_brand_id("Sweethome"))

    assert brand_id == db.winner.id
    assert db.savepoints == 1
    assert db.rolled_back == 0  # savepoint rollback only, not the whole transaction


def test_resolve_brand_id_suffixes_a_taken_slug():
    db = _SlugTakenSession()

    brand_id = asyncio.run(ProductService(db).resolve_brand_id("Sweethome Cement"))

    brand = next(row for row in db.added if isinstance(row, Brand))
    assert brand.slug.startswith("sweethome-cement-")
    assert brand_id == brand.id


def test_create_rejects_an_oversized_brand_name():
    with pytest.raises(ValidationError):
        _payload(brand_name="x" * 256)


def test_create_rejects_an_oversized_unit_of_measure():
    with pytest.raises(ValidationError):
        _payload(unit_of_measure="x" * 51)


def _images(db) -> list:
    return [row for row in db.added if isinstance(row, ProductImage)]


def _specs(db) -> list:
    return [row for row in db.added if isinstance(row, ProductSpecification)]


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
