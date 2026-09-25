"""Guest (anonymous) BOQ access — server-side truncation / masking.

The sign-up wall must live in the API: an unauthenticated request must never
receive the full BOQ or the full verification analysis, because anything the
response contains can be read straight out of the network tab (or the
localStorage entry the chat modal mirrors for the post-login hand-off).

All tests are mocked — no live Gemini, Mongo or Postgres calls.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ── Stubs ────────────────────────────────────────────────────────────────────

class _StubUpload:
    """Minimal UploadFile stand-in."""

    def __init__(self, filename: str, content_type: str, data: bytes = b"x"):
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self):
        return self._data

    async def seek(self, _pos):
        return 0


class _StubRequest:
    """Minimal Request stand-in (no client -> rate-limit key 'unknown')."""

    client = None


def _guest_call(coro):
    """Run an endpoint coroutine with guest rate-limiting stubbed out."""
    with patch("app.api.v1.endpoints.boqs.rate_limit", AsyncMock(return_value=True)):
        return asyncio.run(coro)


# ── Truncation / masking helpers ─────────────────────────────────────────────

def test_truncate_boq_for_guest_limits_items_and_masks_totals():
    from app.api.v1.endpoints.boqs import _truncate_boq_for_guest

    full = {
        "_id": "abc123",
        "elements": [
            {
                "element_name": "Substructure",
                "items": [{"item_code": f"I{i}", "amount": 1000 * i} for i in range(1, 6)],
            }
        ],
        "summary": {"sub_total": 5_234_000, "total_contract_sum": 6_120_000, "total_floor_area_m2": 120},
    }

    result = _truncate_boq_for_guest(full)

    assert result["requires_signup"] is True
    assert result["_id"] is None, "guests must never receive a stored BOQ id"

    items = result["elements"][0]["items"]
    assert len(items) == 4, "3 real items + 1 locked stub"
    assert items[-1]["item_code"] == "..."
    assert "Sign up" in items[-1]["description"]
    assert "2 more" in items[-1]["description"], "the stub states how many are hidden"

    # Money totals are masked, the non-money fields are left alone.
    assert result["summary"]["sub_total"] == 5_000_000
    assert result["summary"]["total_contract_sum"] == 6_000_000
    assert result["summary"]["total_floor_area_m2"] == 120


def test_mask_verification_for_guest_hides_rows_and_masks_total():
    from app.api.v1.endpoints.boqs import _mask_verification_for_guest

    result = {
        "boq_id": "507f1f77bcf86cd799439011",
        "parsed_boq": {
            "items": [{"description": f"Item {i}"} for i in range(1, 7)],
            "total_quoted": 3_450_000,
        },
        "analysis": {
            "verified_items": [{"description": f"Item {i}", "status": "inflated"} for i in range(1, 7)],
            "discrepancies": [{"description": f"D{i}"} for i in range(1, 5)],
        },
        "message": "Verified 6 items.",
    }

    masked = _mask_verification_for_guest(result)

    assert masked["requires_signup"] is True
    assert masked["boq_id"] == "", "guests get no stored BOQ id"
    assert len(masked["parsed_boq"]["items"]) == 4
    assert masked["parsed_boq"]["total_quoted"] == 3_000_000
    assert len(masked["analysis"]["verified_items"]) == 3
    assert len(masked["analysis"]["discrepancies"]) == 2
    assert masked["analysis"]["hidden_items"] == 3

# ── Endpoint behaviour ───────────────────────────────────────────────────────

_CANNED_BOQ = {
    "_id": "should-be-nulled",
    "elements": [
        {"element_name": "Walls", "items": [{"item_code": f"W{i}"} for i in range(1, 8)]}
    ],
    "summary": {"sub_total": 12_345_678, "total_contract_sum": 14_000_000},
}


class _FakeBOQGenerator:
    """Records how the generator was constructed and returns a canned BOQ."""

    last_kwargs: dict = {}
    last_user_id: str = ""

    def __init__(self, db=None, pg_db=None, **kwargs):
        _FakeBOQGenerator.last_kwargs = {"db": db, "pg_db": pg_db}

    async def generate_from_parameters(self, request, user_id):
        _FakeBOQGenerator.last_user_id = user_id
        import copy
        return copy.deepcopy(_CANNED_BOQ)


def test_generate_from_drawing_guest_is_truncated_and_never_persisted():
    """Anonymous drawing upload: free, truncated, nothing written to Mongo."""
    from app.api.v1.endpoints import boqs

    db = MagicMock()
    pg_db = MagicMock()

    class _FakeAI:
        async def analyze_document(self, file_content, file_type, extracted_metadata):
            return {
                "processed": True,
                "rooms": [{"roomName": "Living", "area": 24, "confidence": 0.9}],
                "detectedElements": [{"elementType": "column", "confidence": 0.8}],
                "detectedMaterials": [],
            }

    class _FakeMapper:
        def map(self, extracted_geometry, drawing_quality, project_meta):
            return {
                "needs_manual_fallback": False,
                "request": {"project_info": {"project_title": "Test"}, "floors": []},
            }

    with patch.object(boqs, "AIService", _FakeAI), \
         patch.object(boqs, "BOQGenerator", _FakeBOQGenerator), \
         patch("app.services.drawing_to_boq_mapper.DrawingToBOQMapper", _FakeMapper), \
         patch.object(boqs.TokenService, "deduct_tokens", AsyncMock()) as deduct:
        result = _guest_call(
            boqs.generate_boq_from_drawing(
                http_request=_StubRequest(),
                file=_StubUpload("plan.pdf", "application/pdf"),
                current_user=None,
                db=db,
                pg_db=pg_db,
            )
        )

    # No token deduction and no Mongo write for guests.
    deduct.assert_not_awaited()
    db.__getitem__.assert_not_called()
    assert _FakeBOQGenerator.last_user_id == "anonymous"

    assert result["requires_signup"] is True
    assert result["_id"] is None
    assert len(result["elements"][0]["items"]) == 4


def test_upload_boq_guest_verifies_without_persistence_and_masks():
    """Anonymous Excel upload: DB-priced verification, no storage, masked reply."""
    from app.api.v1.endpoints import boqs

    canned = {
        "boq_id": "stored-id",
        "parsed_boq": {
            "items": [{"description": f"Item {i}"} for i in range(1, 6)],
            "total_quoted": 2_500_000,
        },
        "analysis": {
            "verified_items": [{"description": f"Item {i}", "status": "fair"} for i in range(1, 6)],
            "discrepancies": [],
        },
        "message": "Verified 5 items.",
    }

    captured = {}

    class _FakeVerifyGenerator:
        def __init__(self, db=None, pg_db=None, **kwargs):
            captured["db"] = db
            captured["pg_db"] = pg_db

        async def upload_and_verify(self, file, uploaded_by, **kwargs):
            captured["uploaded_by"] = uploaded_by
            captured["city"] = kwargs.get("city")
            return canned

    pg_db = MagicMock()
    with patch.object(boqs, "BOQGenerator", _FakeVerifyGenerator):
        result = _guest_call(
            boqs.upload_boq(
                http_request=_StubRequest(),
                file=_StubUpload("boq.xlsx", "application/vnd.ms-excel"),
                current_user=None,
                db=MagicMock(),
                pg_db=pg_db,
            )
        )

    assert captured["db"] is None, "guest verification must not write to Mongo"
    assert captured["pg_db"] is pg_db, "DB market rates are still used for guests"
    assert captured["uploaded_by"] == "anonymous"

    assert result["requires_signup"] is True
    assert result["boq_id"] == ""
    assert len(result["parsed_boq"]["items"]) == 4
    assert result["parsed_boq"]["total_quoted"] == 2_000_000

