"""Large-file efficiency: streamed parsing, bounded rate lookups, background jobs.

Phase 6 keeps a big bill (a full QS workbook) from blocking a request or the
event loop:

* `parse_boq_file` consumes sheets one at a time instead of building every
  sheet's rows up front;
* market-rate lookups are dispatched in bounded batches rather than gathering
  the whole target list at once;
* `POST /boqs/verify/async` queues the work (Celery `boq` queue) and reports
  through a pollable job document, with an inline fallback when no worker is
  reachable.

No database, broker or AI key is needed: the job runner is exercised against a
fake async Mongo and a real temp file.
"""
import asyncio
from tempfile import SpooledTemporaryFile
from unittest.mock import AsyncMock, MagicMock

from bson import ObjectId

from app.services import boq_generator as gen
from app.services.boq_generator import BOQGenerator
from app.tasks import boq_generation_tasks as tasks

JOB_ID = str(ObjectId("64b000000000000000000042"))


def _run(coro):
    return asyncio.run(coro)


def _fake_jobs():
    """A fake `boq_jobs` collection that records every status transition."""
    statuses = []

    async def record(query, update):
        statuses.append(update["$set"]["status"])

    jobs = MagicMock()
    jobs.update_one = AsyncMock(side_effect=record)

    db = MagicMock()
    db.__getitem__.side_effect = (
        lambda name: jobs if name == tasks._BOQ_JOB_COLLECTION else MagicMock()
    )
    return db, jobs, statuses


# ── Background job lifecycle ──────────────────────────────────────────────────

def test_job_publishes_the_result_and_removes_the_upload(tmp_path, monkeypatch):
    path = tmp_path / "bill.xlsx"
    path.write_bytes(b"bill-bytes")
    db, jobs, statuses = _fake_jobs()

    async def fake_verify(self, file, uploaded_by, city="Abuja"):
        assert file.filename == "bill.xlsx"
        assert file.read() == b"bill-bytes"
        assert uploaded_by == "user-1"
        return {"boq_id": "boq-1", "message": "Verified 2 items."}

    monkeypatch.setattr(BOQGenerator, "upload_and_verify", fake_verify)

    result = _run(
        tasks.run_verify_job(db, JOB_ID, str(path), "bill.xlsx", "Lagos", "user-1")
    )

    assert result["boq_id"] == "boq-1"
    assert statuses == ["running", "succeeded"]
    assert jobs.update_one.await_args.args[1]["$set"]["result"]["boq_id"] == "boq-1"
    # The temp upload is deleted with the job, so large bills cannot pile up.
    assert not path.exists()


def test_job_records_a_failure_instead_of_staying_running(tmp_path, monkeypatch):
    path = tmp_path / "bill.xlsx"
    path.write_bytes(b"bill-bytes")
    db, jobs, statuses = _fake_jobs()

    async def boom(self, file, uploaded_by, city="Abuja"):
        raise RuntimeError("price service unavailable")

    monkeypatch.setattr(BOQGenerator, "upload_and_verify", boom)

    result = _run(tasks.run_verify_job(db, JOB_ID, str(path), "bill.xlsx"))

    assert result["status"] == "failed"
    assert "price service unavailable" in result["error"]
    assert statuses == ["running", "failed"]
    assert jobs.update_one.await_args.args[1]["$set"]["error"] == "price service unavailable"
    assert not path.exists()


def test_job_fails_loudly_when_the_upload_is_gone(tmp_path):
    db, jobs, statuses = _fake_jobs()

    result = _run(
        tasks.run_verify_job(db, JOB_ID, str(tmp_path / "gone.xlsx"), "gone.xlsx")
    )

    assert result["status"] == "failed"
    assert statuses == ["running", "failed"]


def test_queued_task_verifies_off_the_request_thread(tmp_path, monkeypatch):
    """The Celery entry point resolves the runner's dependencies itself."""
    path = tmp_path / "bill.csv"
    path.write_bytes(b"Item Ref,Description,Qty,Unit,Rate,Amount\nA,Cement,2,bag,500,1000\n")

    recorded = {}

    async def fake_runner(db, job_id, path, filename, city="Abuja", user_id=""):
        recorded.update({"job_id": job_id, "filename": filename, "city": city})
        return {"boq_id": "boq-9"}

    monkeypatch.setattr(tasks, "run_verify_job", fake_runner)
    monkeypatch.setattr(tasks.core_database, "mongodb", MagicMock(name="db"))

    assert tasks.verify_boq_task(JOB_ID, str(path), "bill.csv", "Kano", "user-2") == {
        "status": "success", "job_id": JOB_ID
    }
    assert recorded["filename"] == "bill.csv"
    assert recorded["city"] == "Kano"


def test_a_missing_broker_falls_back_inline_instead_of_hanging(monkeypatch):
    """A dev machine with no Redis must not stall the request on a publish.

    The endpoint runs the job inline when the broker cannot be reached; the
    1-second probe below is what makes that decision fast (a bare `apply_async`
    against a dead broker retries the connection for tens of seconds).
    """
    from app.api.v1.endpoints import boqs

    monkeypatch.setattr(boqs.settings, "CELERY_BROKER_URL", "redis://127.0.0.1:1/1")

    assert _run(boqs._broker_reachable()) is False
    assert _run(
        boqs._dispatch_verify_job(JOB_ID, "x.xlsx", "x.xlsx", "Abuja", "u")
    ) is False


# ── Streamed parsing ──────────────────────────────────────────────────────────

def test_sheets_are_parsed_without_loading_the_whole_workbook(monkeypatch):
    """While sheet 1 is parsed, sheet 2's rows must not exist yet.

    The previous reader built every sheet's rows into one list before parsing
    began, which is what made a large workbook cost many times its file size.
    """
    from app.services import boq_file_parser as parser

    produced = []
    observed = {}

    def fake_sheets(content, filename, warnings):
        for index in (1, 2):
            produced.append(index)
            yield f"ELEMENT NR.{index} TEST", [
                ["Item Ref", "Description", "Qty", "Unit", "Rate", "Amount"],
                ["A", f"Item {index}", "1", "nr", "100", "100"],
            ]

    real_parse = parser._parse_element_sheet

    def spy(rows, columns, element_name, sheet_name, start_row=0):
        # First call only: for sheet 2 `produced` is expected to have grown.
        observed.setdefault("produced_when_first_parsed", len(produced))
        return real_parse(rows, columns, element_name, sheet_name, start_row=start_row)

    monkeypatch.setattr(parser, "_iter_sheets", fake_sheets)
    monkeypatch.setattr(parser, "_parse_element_sheet", spy)

    parsed = parser.parse_boq_file(b"irrelevant", "bill.xlsx")

    assert observed["produced_when_first_parsed"] == 1
    assert len(parsed["items"]) == 2
    assert len(parsed["sheets"]) == 2


def test_unreadable_file_still_reports_a_warning_not_an_exception():
    from app.services.boq_file_parser import parse_boq_file

    parsed = parse_boq_file(b"nope", "bill.txt")

    assert parsed["items"] == []
    assert parsed["sheets"] == []
    assert any("Unsupported file format" in warning for warning in parsed["warnings"])


# ── Bounded rate lookups ──────────────────────────────────────────────────────

def _csv_upload(rows):
    """A spooled upload shaped like the one the verify endpoint hands over."""
    upload = SpooledTemporaryFile()
    upload.write("\n".join(rows).encode("utf-8"))
    upload.seek(0)
    upload.filename = "bill.csv"
    return upload


def test_rate_lookups_are_dispatched_in_bounded_batches(monkeypatch):
    """120 distinct lines must be looked up 50 at a time, not in one gather."""
    rows = ["Item Ref,Description,Qty,Unit,Rate,Amount"]
    rows += [f"A,Item {index},2,nr,500,1000" for index in range(1, 121)]
    upload = _csv_upload(rows)

    lookups = []

    async def fake_rate(self, description, city="Abuja"):
        lookups.append(description)
        return {
            "rate": 500.0, "city": city, "source": "database",
            "product_name": description,
        }

    batch_sizes = []
    real_gather = asyncio.gather

    async def spy_gather(*awaitables, **kwargs):
        batch_sizes.append(len(awaitables))
        return await real_gather(*awaitables, **kwargs)

    monkeypatch.setattr(gen.PriceTruthService, "get_verified_rate", fake_rate)
    monkeypatch.setattr(gen.asyncio, "gather", spy_gather)

    try:
        generator = gen.BOQGenerator(db=None)
        generator.api_key = ""  # deterministic: skip the optional Gemini pass
        result = _run(generator.upload_and_verify(upload, uploaded_by="user-1"))
    finally:
        upload.close()

    assert len(lookups) == 120
    assert [size for size in batch_sizes if size] == [50, 50, 20]
    assert result["analysis"]["verified_count"] == 120


def test_a_repeated_description_is_only_looked_up_once(monkeypatch):
    """Repeated lines share a single lookup — batching must not lose that."""
    rows = ["Item Ref,Description,Qty,Unit,Rate,Amount"]
    rows += ["A,Cement,2,bag,500,1000"] * 40
    upload = _csv_upload(rows)

    lookups = []

    async def fake_rate(self, description, city="Abuja"):
        lookups.append(description)
        return {
            "rate": 500.0, "city": city, "source": "database",
            "product_name": description,
        }

    monkeypatch.setattr(gen.PriceTruthService, "get_verified_rate", fake_rate)

    try:
        generator = gen.BOQGenerator(db=None)
        generator.api_key = ""
        result = _run(generator.upload_and_verify(upload, uploaded_by="user-1"))
    finally:
        upload.close()

    assert len(lookups) == 1
    assert len(result["analysis"]["verified_items"]) == 40
