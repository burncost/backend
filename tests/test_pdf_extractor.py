"""The PDF reader must report the document it was given — never invent one.

`PDFExtractor` used to return a hard-coded ``pageCount`` of 5, an "Architect
Name" author and a fabricated "bill of quantities" table, which the document
pipeline fed to the AI analysis as if it had been read from the upload. These
tests build a real PDF with reportlab and assert the values come from it.
"""
import asyncio
import io

from app.services.pdf_extractor import PDFExtractor

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _pdf_bytes(pages: int = 2) -> bytes:
    """A real PDF with a ruled BOQ table on page 1 and `pages` total pages."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Table,
        TableStyle,
    )

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title="Main Bill",
        author="Sample QS Ltd",
        subject="Bill of Quantities",
        keywords="blockwork, substructure",
    )
    styles = getSampleStyleSheet()
    rows = [
        ["Item Code", "Description", "Qty", "Unit", "Rate", "Amount"],
        ["A", "Excavate trenches", "120", "m3", "4500", "540000"],
        ["B", "Plain concrete", "18", "m3", "85000", "1530000"],
    ]
    table = Table(rows)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story = [Paragraph("Bill of Quantities", styles["Title"]), table]
    for index in range(1, pages):
        story.extend([PageBreak(), Paragraph(f"Page {index + 1}", styles["Title"])])
    document.build(story)
    return buffer.getvalue()


def test_extract_reports_the_real_page_count_and_metadata():
    result = asyncio.run(PDFExtractor().extract(_pdf_bytes(pages=2)))

    assert result["pageCount"] == 2  # not the old hard-coded 5
    assert result["author"] == "Sample QS Ltd"
    assert result["title"] == "Main Bill"
    assert result["subject"] == "Bill of Quantities"
    assert result["keywords"] == ["blockwork", "substructure"]


def test_extract_returns_the_documents_own_table():
    result = asyncio.run(PDFExtractor().extract(_pdf_bytes()))

    assert result["tables"], "the ruled table on page 1 should be detected"
    table = result["tables"][0]
    assert table["pageNumber"] == 1
    assert table["detectedType"] == "bill_of_quantities"
    flat = " ".join(cell for row in table["tableData"] for cell in row)
    assert "Excavate trenches" in flat
    assert "Cement" not in flat  # the fabricated sample table is gone


def test_extract_reports_failure_instead_of_fabricating_content():
    result = asyncio.run(PDFExtractor().extract(b"this is not a pdf at all"))

    assert result["pageCount"] == 0
    assert result["tables"] == []
    assert result["errors"]


def test_thumbnail_is_a_png_of_the_first_page():
    thumbnail = asyncio.run(PDFExtractor().generate_thumbnail(_pdf_bytes()))

    if thumbnail is None:  # PyMuPDF missing in this environment
        return
    assert thumbnail.startswith(PNG_SIGNATURE)


def test_thumbnail_returns_none_rather_than_raising_on_a_bad_pdf():
    assert asyncio.run(PDFExtractor().generate_thumbnail(b"not a pdf")) is None
