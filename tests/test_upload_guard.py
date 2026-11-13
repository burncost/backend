"""Content-level upload checks: signature sniffing and archive-bomb guards.

Both BOQ upload paths previously trusted the filename extension and the declared
MIME type. These tests pin the checks that now look at the bytes themselves, and
pin that a rejected upload never disturbs the caller's stream position (the
parser is handed the same object straight afterwards).
"""
import io
import zipfile

from app.services.upload_guard import inspect_upload

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32


def _xlsx_bytes() -> bytes:
    """A real, minimal .xlsx built by the same writer the parser uses."""
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "Description"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _docx_bytes() -> bytes:
    """A real, minimal .docx built by the same writer the parser uses."""
    from docx import Document

    document = Document()
    document.add_paragraph("A bill")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ── signature checks ─────────────────────────────────────────────────────────

def test_a_renamed_image_is_rejected_as_a_workbook():
    """`bill.xlsx` that is really a PNG must not reach openpyxl."""
    reason = inspect_upload(io.BytesIO(PNG), "bill.xlsx")

    assert reason is not None
    assert "PNG" in reason  # names what it actually is


def test_a_real_workbook_passes():
    assert inspect_upload(io.BytesIO(_xlsx_bytes()), "bill.xlsx") is None


def test_a_real_word_document_passes():
    assert inspect_upload(io.BytesIO(_docx_bytes()), "bill.docx") is None


def test_a_match_on_the_name_is_not_required_for_an_unlabelled_file():
    """A PDF named `plan` (no extension) is still a valid drawing."""
    assert inspect_upload(io.BytesIO(PDF), "plan") is None
    assert inspect_upload(io.BytesIO(PDF), None) is None


def test_an_unrecognisable_file_is_rejected():
    assert inspect_upload(io.BytesIO(b"\x00\x01\x02\x03 rkhg"), "bill.pdf") is not None


def test_an_empty_upload_is_rejected():
    assert inspect_upload(io.BytesIO(b""), "bill.xlsx") is not None


def test_a_text_csv_passes_but_binary_bytes_disguised_as_csv_do_not():
    assert inspect_upload(io.BytesIO(b"Description,Qty,Rate\nBlockwork,10,9000\n"), "bill.csv") is None
    assert inspect_upload(io.BytesIO(b"Description\x00\x00Qty"), "bill.csv") is not None


# ── archive-bomb guards ──────────────────────────────────────────────────────

def _zeros_zip(uncompressed_bytes: int, chunk: int = 1024 * 1024) -> bytes:
    """A zip holding `uncompressed_bytes` of zeros — what a bomb looks like."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        with archive.open("payload.bin", "w") as entry:
            written = 0
            while written < uncompressed_bytes:
                size = min(chunk, uncompressed_bytes - written)
                entry.write(b"\x00" * size)
                written += size
    return buffer.getvalue()


def test_a_workbook_expanding_past_the_cap_is_rejected():
    """A 2 MB archive that declares 8 MB uncompressed, against a 1 MB cap."""
    payload = _zeros_zip(8 * 1024 * 1024)

    reason = inspect_upload(io.BytesIO(payload), "bill.xlsx", max_uncompressed=1024 * 1024)

    assert reason is not None
    assert "too large to process" in reason


def test_a_compression_ratio_bomb_is_rejected(monkeypatch):
    """2 MB of zeros in ~2 KB of archive is a ratio no spreadsheet produces.

    The production ratio gate deliberately ignores *small* archives (a 1 KB zip
    legitimately expands ~40x), so the byte threshold is lowered here rather than
    compressing 100 MB of zeros just to reach it in a test.
    """
    from app.services import upload_guard

    monkeypatch.setattr(upload_guard, "_MIN_RATIO_BYTES", 0)
    payload = _zeros_zip(2 * 1024 * 1024)

    reason = inspect_upload(io.BytesIO(payload), "bill.xlsx")

    assert reason is not None
    assert "archive bomb" in reason


def test_a_workbook_with_absurdly_many_parts_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(5):
            archive.writestr(f"sheet{index}.xml", "<x/>")

    reason = inspect_upload(io.BytesIO(buffer.getvalue()), "bill.xlsx", max_entries=3)

    assert reason is not None
    assert "internal parts" in reason


def test_the_inspection_leaves_the_stream_ready_for_the_parser():
    """The parser is handed the same stream: it must still start at byte 0."""
    stream = io.BytesIO(_xlsx_bytes())

    assert inspect_upload(stream, "bill.xlsx") is None
    assert stream.tell() == 0


# ── the shared drawing path ──────────────────────────────────────────────────

def test_drawing_uploads_are_streamed_and_content_checked():
    """`_read_upload_checked` bounds the read *and* verifies what was read."""
    import asyncio

    import pytest
    from fastapi import HTTPException

    from app.api.v1.endpoints import boqs

    class _Upload:
        """Just enough of Starlette's UploadFile for the helper."""

        def __init__(self, data: bytes, filename: str):
            self._buffer = io.BytesIO(data)
            self.filename = filename

        async def read(self, size: int = -1) -> bytes:
            return self._buffer.read(size)

    # A PNG renamed .pdf never reaches the AI — it is refused as mismatched.
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(boqs._read_upload_checked(_Upload(PNG, "plan.pdf"), 1024))
    assert rejected.value.status_code == 400
    assert "PNG" in rejected.value.detail

    # A genuine drawing is handed back as bytes.
    assert asyncio.run(boqs._read_upload_checked(_Upload(PDF, "plan.pdf"), 1024)) == PDF

    # An oversize body is stopped as soon as the cap is passed, not buffered whole.
    with pytest.raises(HTTPException) as too_big:
        asyncio.run(boqs._read_upload_checked(_Upload(PDF, "plan.pdf"), 4))
    assert too_big.value.status_code == 413

