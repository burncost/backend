"""Admin demand-broadcast agreement visibility.

Contract pinned here:
  * `_broadcast_dict` exposes the agreement (vendor, price, timestamp) and flags
    the row `is_agreed`; an agreed row can never be reopened by ops.
  * `expires_at` is only reported for alerts raised before the TTL freeze, so the
    console stops showing a countdown on broadcasts that cannot expire.
"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.endpoints.admin_demand import AGREED_STATUS, CLOSED_STATUS, _broadcast_dict
from app.models.demand_alert import DEMAND_ALERT_TTL_FROZEN_AT


def _alert(**overrides):
    base = dict(
        id=uuid4(),
        item_description="9-inch sandcrete block (225mm)",
        city="Abuja",
        quantity_needed=8400,
        unit="nr",
        project_title="Duplex",
        status="pending",
        created_at=DEMAND_ALERT_TTL_FROZEN_AT + timedelta(days=1),
        updated_at=None,
        material_key="blocks_225",
        spec="225mm",
        source_ref="takeoff:blocks_225",
        agreed_vendor_id=None,
        agreed_price=None,
        agreed_at=None,
        agreed_by=None,
        requested_by=uuid4(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_agreement_fields_are_serialized():
    vendor_id = uuid4()
    alert = _alert(
        status=AGREED_STATUS,
        agreed_vendor_id=vendor_id,
        agreed_price=450,
        agreed_at=DEMAND_ALERT_TTL_FROZEN_AT,
        agreed_by="builder",
    )

    payload = _broadcast_dict(alert, "Ada Builder", agreed_vendor="Acme Blocks")

    assert payload["status"] == AGREED_STATUS
    assert payload["is_agreed"] is True
    assert payload["is_closed"] is False
    assert payload["agreed_vendor_id"] == str(vendor_id)
    assert payload["agreed_vendor"] == "Acme Blocks"
    assert payload["agreed_price"] == 450.0
    assert payload["agreed_by"] == "builder"
    # Take-off identity is what makes the line traceable back to the BOQ.
    assert payload["material_key"] == "blocks_225"
    assert payload["spec"] == "225mm"


def test_live_broadcast_has_no_expiry_deadline():
    payload = _broadcast_dict(_alert())

    assert payload["is_expired"] is False
    assert payload["expires_at"] is None


def test_legacy_broadcast_keeps_its_historical_deadline():
    created = DEMAND_ALERT_TTL_FROZEN_AT - timedelta(days=10)
    payload = _broadcast_dict(_alert(created_at=created))

    assert payload["is_expired"] is True
    assert payload["expires_at"] is not None


def test_closed_broadcast_is_flagged():
    payload = _broadcast_dict(_alert(status=CLOSED_STATUS))

    assert payload["is_closed"] is True
    assert payload["is_agreed"] is False
