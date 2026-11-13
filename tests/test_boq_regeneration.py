"""Regenerate-on-accept for uploaded bills (Phase 5).

An uploaded-for-verification bill lives in `boq_verifications`; accepting it
must rebuild a real, priced BOQ (a new `boqs` version) from the verified items,
not just flip a status. These tests use a fake async Mongo and the real
`PriceService.recalculate_totals`, so regeneration is checked end to end
without a database or an AI key.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bson import ObjectId

from app.services.boq_generator import (
    BOQGenerator,
    _rate_lookup,
    _regenerated_elements,
    _reprice_lines,
)

VERIFICATION_ID = ObjectId("64b000000000000000000001")


def _run(coro):
    return asyncio.run(coro)


def _verification(**over):
    """An uploaded bill: two items, only one of which has a comparable rate."""
    doc = {
        "_id": VERIFICATION_ID,
        "filename": "bill.xlsx",
        "uploadedBy": "user-1",
        "city": "Abuja",
        "version": 1,
        "items": [
            {
                "description": "Cement", "element_name": "Substructure",
                "unit": "bag", "quantity": 10, "rate": 9000.0, "amount": 90000.0,
            },
            {
                "description": "Sharp sand", "element_name": "Substructure",
                "unit": "tonne", "quantity": 2, "rate": 30000.0, "amount": 60000.0,
            },
        ],
        "analysis": {
            "verified_items": [
                {
                    "description": "Cement",
                    "market_rate": 8000.0,
                    "market_rate_city": "Abuja",
                    "market_rate_source": "database",
                },
            ]
        },
    }
    doc.update(over)
    return doc


def _service(verification):
    """A BOQGenerator over a fake db; no AI key, so the rebuild is deterministic."""
    verifications = MagicMock()
    verifications.find_one = AsyncMock(return_value=verification)
    verifications.update_one = AsyncMock()

    boqs = MagicMock()
    boqs.insert_one = AsyncMock(return_value=SimpleNamespace(inserted_id="new-boq-id"))
    boqs.find_one_and_update = AsyncMock(return_value=None)

    collections = {"boq_verifications": verifications, "boqs": boqs}
    db = MagicMock()
    db.__getitem__.side_effect = collections.__getitem__

    service = BOQGenerator(db=db)
    service.api_key = ""  # deterministic: skip the optional Gemini wording pass
    return service, verifications, boqs


# ── Repricing ─────────────────────────────────────────────────────────────────

def test_verified_market_rate_replaces_the_quoted_rate():
    corrected = _reprice_lines(
        _verification()["items"],
        _rate_lookup(_verification()["analysis"]["verified_items"]),
        "Abuja",
    )

    cement = corrected[0]
    assert cement["rate"] == 8000.0
    assert cement["amount"] == 80000.0
    assert cement["original_rate"] == 9000.0
    assert cement["was_revised"] is True


def test_quantities_are_never_touched_by_regeneration():
    corrected = _reprice_lines(
        _verification()["items"],
        _rate_lookup(_verification()["analysis"]["verified_items"]),
        "Abuja",
    )

    assert [line["quantity"] for line in corrected] == [10, 2]


def test_line_without_a_comparable_rate_keeps_its_quoted_figure():
    corrected = _reprice_lines(
        _verification()["items"],
        _rate_lookup(_verification()["analysis"]["verified_items"]),
        "Abuja",
    )

    sand = corrected[1]
    assert sand["rate"] == 30000.0
    assert sand["was_revised"] is False
    assert sand["rate_source"] == "quoted"
    assert "kept" in sand["revision_note"]


def test_elements_carry_original_and_regenerated_totals():
    corrected = _reprice_lines(
        _verification()["items"],
        _rate_lookup(_verification()["analysis"]["verified_items"]),
        "Abuja",
    )

    element = _regenerated_elements(corrected)[0]
    assert element["original_total"] == 150000.0
    assert element["regenerated_total"] == 140000.0
    assert element["variance"] == -10000.0


# ── Decision dispatch ─────────────────────────────────────────────────────────

def test_regenerate_rebuilds_a_priced_boq_and_saves_a_new_version():
    service, verifications, boqs = _service(_verification())

    result = _run(service.handle_decision(str(VERIFICATION_ID), "regenerate", "user-1"))

    assert result["status"] == "regenerated"
    assert result["record_type"] == "verification"
    regen = result["regenerated_boq"]
    assert regen["summary"]["original_sub_total"] == 150000.0
    assert regen["summary"]["regenerated_sub_total"] == 140000.0
    assert regen["summary"]["total_saving"] == 10000.0
    # Contract-level comparison: sub-total pushed through the same totals maths
    # (contingency 10% + overheads/profit 10% + VAT 7.5%).
    assert regen["summary"]["original_contract_sum"] == 193500.0
    assert regen["summary"]["regenerated_contract_sum"] == 180600.0
    assert regen["summary"]["contract_saving"] == 12900.0
    assert regen["items_revised"] == 1

    stored = boqs.insert_one.await_args.args[0]
    assert stored["generationMethod"] == "regenerated"
    assert stored["sourceVerificationId"] == str(VERIFICATION_ID)
    assert stored["summary"]["sub_total"] == 140000.0

    # The verification record is stamped, not silently left pending.
    update = verifications.update_one.await_args
    assert update.args[1]["$set"]["status"] == "regenerated"
    assert update.args[1]["$set"]["regeneratedBoqId"] == "new-boq-id"


def test_save_original_records_the_choice_without_rebuilding():
    service, verifications, boqs = _service(_verification())

    result = _run(service.handle_decision(str(VERIFICATION_ID), "save_original", "user-1"))

    assert result["status"] == "saved_original"
    assert boqs.insert_one.await_count == 0
    assert verifications.update_one.await_args.args[1]["$set"]["userDecision"] == "save_original"


def test_decision_on_someone_elses_upload_is_refused():
    service, verifications, boqs = _service(_verification(uploadedBy="someone-else"))

    result = _run(service.handle_decision(str(VERIFICATION_ID), "regenerate", "user-1"))

    assert result is None
    assert boqs.insert_one.await_count == 0
    assert verifications.update_one.await_count == 0


def test_malformed_id_returns_none_instead_of_raising():
    service, _, _ = _service(_verification())

    assert _run(service.handle_decision("not-an-object-id", "regenerate", "user-1")) is None


def test_generated_bill_still_only_flips_status():
    service, _, boqs = _service(None)
    boqs.find_one_and_update = AsyncMock(
        return_value={"_id": "boq-1", "status": "regenerated"}
    )

    result = _run(service.handle_decision("64b000000000000000000002", "regenerate", "user-1"))

    assert result["status"] == "regenerated"
    assert boqs.insert_one.await_count == 0
    assert boqs.find_one_and_update.await_args.args[1]["$set"]["userDecision"] == "regenerate"

