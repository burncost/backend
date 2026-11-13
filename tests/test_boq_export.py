"""Branded BOQ export: PDF, Word, Excel and CSV.

Locks the promises the download buttons make:

  1. The PDF is never empty - it always carries the title block and the summary,
     even for a bill with no measurable items (the bug that made the old
     reportlab export look blank).
  2. The PDF carries the BURNCOST watermark and a footer on every page, matching
     the watermarked vendor report.
  3. The Word export stamps the same watermark into the section header and ends
     with the orange TOTAL CONTRACT SUM row.
  4. Excel keeps the BurnCost orange header row and CSV still streams.

Uses the fake async Mongo from `test_boq_regeneration` (real exporters, no DB).
"""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fitz  # PyMuPDF
from bson import ObjectId

from app.services.boq_generator import BOQGenerator

BOQ_ID = ObjectId("64b000000000000000000777")


def _boq(**over):
    doc = {
        "_id": BOQ_ID,
        "title": "4 Bedroom Duplex",
        "boqData": {
            "project_info": {
                "project_title": "4 Bedroom Duplex",
                "client": "Mr Ade",
                "reference": "BC-2026-001",
                "location": "Lagos",
                "date": "2026-10-01",
            },
            "elements": [
                {
                    "elementName": "SUBSTRUCTURE",
                    "items": [
                        {
                            "item_code": "SUB-01",
                            "description": "Excavation to 1.2m \u2014 laterite",
                            "quantity": 297.07,
                            "unit": "m3",
                            "adjusted_rate": 95000,
                            "amount": 28_221_650.0,
                        },
                    ],
                },
            ],
            "summary": {
                "sub_total": 28_221_650.0,
                "contingency_pct": 10,
                "contingency_amount": 2_822_165.0,
                "overheads_profit_pct": 10,
                "overheads_profit_amount": 2_822_165.0,
                "vat_pct": 7.5,
                "vat_amount": 2_541_373.5,
                "total_contract_sum": 36_107_353.5,
            },
        },
    }
    doc.update(over)
    return doc


def _service(boq):
    boqs = MagicMock()
    boqs.find_one = AsyncMock(return_value=boq)
    verifications = MagicMock()
    verifications.find_one = AsyncMock(return_value=None)
    collections = {"boqs": boqs, "boq_verifications": verifications}
    db = MagicMock()
    db.__getitem__.side_effect = collections.__getitem__
    service = BOQGenerator(db=db)
    service.api_key = ""
    return service


def _export(boq, fmt):
    out = asyncio.run(_service(boq).export_boq(str(BOQ_ID), fmt))
    assert out is not None
    return out


def test_pdf_export_is_branded_and_never_empty():
    filename, media_type, content = _export(_boq(), "pdf")
    assert filename.endswith(".pdf")
    assert media_type == "application/pdf"
    assert len(content) > 2000, "a branded BOQ PDF must carry real content"

    document = fitz.open(stream=content, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    assert "BILL OF QUANTITIES (BOQ)" in text
    assert "TOTAL CONTRACT SUM" in text
    assert "NGN" in text and "\u20a6" not in text  # base-14 fonts have no naira sign
    assert "SUB-01" in text and "SUBSTRUCTURE" in text


def test_pdf_watermarks_every_page_and_numbers_them():
    document = fitz.open(stream=_export(_boq(), "pdf")[2], filetype="pdf")
    assert document.page_count >= 1
    for index, page in enumerate(document, start=1):
        text = page.get_text()
        assert "BURNCOST" in text, "every page must carry the watermark"
        assert f"Page {index} of {document.page_count}" in text


def test_pdf_still_renders_summary_for_an_empty_bill():
    """No measurable items must not produce a blank download."""
    empty = _boq(boqData={"elements": [], "summary": {"total_contract_sum": 0}})
    content = _export(empty, "pdf")[2]
    document = fitz.open(stream=content, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    assert "TOTAL CONTRACT SUM" in text
    assert "No measurable items" in text
    assert len(content) > 1500


def test_docx_export_is_watermarked_and_has_the_total_row():
    from docx import Document

    filename, media_type, content = _export(_boq(), "word")
    assert filename.endswith(".docx")
    assert "wordprocessingml" in media_type

    document = Document(io.BytesIO(content))
    assert "BURNCOST" in document.sections[0].header._element.xml
    assert len(document.tables) >= 2  # element grid + summary grid

    texts = [p.text for p in document.paragraphs]
    assert "BILL OF QUANTITIES (BOQ)" in texts
    assert any("TOTAL CONTRACT SUM" in cell.text for table in document.tables for row in table.rows for cell in row.cells)


def test_excel_header_uses_the_burncost_orange():
    import openpyxl

    filename, _media_type, content = _export(_boq(), "excel")
    assert filename.endswith(".xlsx")
    workbook = openpyxl.load_workbook(io.BytesIO(content))
    sheet = workbook["BOQ"]
    header = sheet.cell(row=3, column=1)
    assert "FF6B00" in str(header.fill.fgColor.rgb)
    assert header.font.color.rgb.endswith("FFFFFF")


def test_csv_export_still_streams_every_line():
    filename, media_type, content = _export(_boq(), "csv")
    assert filename.endswith(".csv")
    assert media_type == "text/csv"
    text = content.decode("utf-8-sig")
    assert "SUB-01" in text
    assert "Total Contract Sum" in text
