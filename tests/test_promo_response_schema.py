"""Response-schema contract for promo codes, plus a guard against the bug class.

Regression pin: `PromoCodeResponse` is built from ORM rows whose `id`, `vendor_id`
and `scope_id` are PostgreSQL UUID columns. They were typed `str`, which Pydantic
v2 does not coerce from a UUID, so FastAPI raised `ResponseValidationError` (500)
*after* the handler had already committed the promo. The JSON shape is what the
front-ends consume, so that is what these tests pin.
"""

import importlib
import inspect
import pkgutil
import typing
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from pydantic import BaseModel

from app.schemas.promo import PromoCodeResponse


def _orm_like(**overrides):
    """A stand-in for a `PromoCode` row: UUID ids, Decimal money columns."""
    base = dict(
        id=uuid4(),
        code="SAVE1000",
        description=None,
        discount_amount=Decimal("1000.00"),
        max_uses=0,
        current_uses=0,
        max_uses_per_user=0,
        min_order_amount=Decimal("0.00"),
        is_active=True,
        vendor_id=uuid4(),
        scope_type="platform",
        scope_id=uuid4(),
        expires_at=None,
        created_at=datetime.utcnow(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_uuid_columns_serialise_to_json_strings():
    """The exact regression: a real row must not blow up FastAPI serialisation."""
    promo = PromoCodeResponse.model_validate(_orm_like())
    payload = promo.model_dump(mode="json")

    assert isinstance(payload["id"], str)
    assert isinstance(payload["vendor_id"], str)
    assert isinstance(payload["scope_id"], str)
    # Wire format is the canonical dashed UUID the front-ends already parse.
    assert UUID(payload["id"]) == promo.id
    assert payload["discount_amount"] == 1000.0


def test_null_vendor_and_scope_stay_null():
    """Platform promos carry no vendor/scope id."""
    promo = PromoCodeResponse.model_validate(_orm_like(vendor_id=None, scope_id=None))
    payload = promo.model_dump(mode="json")

    assert payload["vendor_id"] is None
    assert payload["scope_id"] is None


def _is_str_annotation(annotation) -> bool:
    if annotation is str:
        return True
    return typing.get_origin(annotation) is typing.Union and str in typing.get_args(annotation)


def _orm_backed_schemas():
    """Every `BaseModel` in `app.schemas` configured with `from_attributes=True`."""
    package = importlib.import_module("app.schemas")
    for info in pkgutil.iter_modules(package.__path__, prefix="app.schemas."):
        module = importlib.import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseModel) or obj is BaseModel:
                continue
            if obj.__module__ != module.__name__:
                continue  # skip classes merely re-exported into this module
            if obj.model_config.get("from_attributes"):
                yield f"{module.__name__}.{obj.__name__}", obj


def test_no_orm_backed_schema_types_an_id_column_as_str():
    """Guard the audit: ORM rows hand Pydantic UUIDs, so id/*_id must not be `str`.

    Mongo-backed / dict-built schemas don't set `from_attributes`, so a genuinely
    string id (e.g. an ObjectId) is unaffected by this check.
    """
    offenders = [
        f"{name}.{field_name}"
        for name, model in _orm_backed_schemas()
        for field_name, field in model.model_fields.items()
        if (field_name == "id" or field_name.endswith("_id"))
        and _is_str_annotation(field.annotation)
    ]

    assert not offenders, f"UUID columns typed as str in ORM-backed schemas: {offenders}"
