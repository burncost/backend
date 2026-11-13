"""Word (.docx) and PDF (.pdf) BOQ ingestion.

Phase 3 lets the verify endpoint accept Word/PDF bills; this covers the readers
that flatten them into the same (sheet name, rows) shape the Excel path uses, so
element grouping, subtotal handling and rate verification are shared unchanged —
and verification stays AI-free. Fixtures are built in memory with python-docx /
reportlab, so the suite needs no sample binaries.
"""
import io

from app.services.boq_file_parser import SUPPORTED_EXTENSIONS, parse_boq_file


def _docx_bytes():
    """A Word bill: cover, one element table, then a general-summary table."""
    from docx import Document

    document = Document()
    document.add_paragraph("COVER PAGE")
    document.add_paragraph("ELEMENT NR.3 CONCRETE")
    table = document.add_table(rows=1, cols=6)
    header = ["Item Ref", "Description", "Qty", "Unit", "Rate", "Amount"]
    for cell, text in zip(table.rows[0].cells, header):
        cell.text = text
    for values in (
        ("A", "Concrete 1:3:6 in foundations", "20", "m3", "55,000.00", "1,100,000.00"),
        ("B", "Reinforcement to foundations", "1,500", "kg", "1,200.00", "1,800,000.00"),
    ):
        for cell, text in zip(table.add_row().cells, values):
            cell.text = text
    document.add_paragraph("GENERAL SUMMARY")
    summary = document.add_table(rows=0, cols=2)
    for label, value in (("Sub-Total", "2,900,000.00"), ("Total Contract Sum", "2,900,000.00")):
        row = summary.add_row().cells
        row[0].text = label
        row[1].text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _pdf_bytes():
    """A PDF bill: a ruled element table under its own element heading."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    data = [
        ["Item Ref", "Description", "Qty", "Unit", "Rate", "Amount"],
        ["A", "Blockwork in hollow sandcrete 225mm", "45", "m2", "4,500.00", "202,500.00"],
        ["B", "Reinforcement to blockwork", "300", "kg", "1,200.00", "360,000.00"],
    ]
    table = Table(data)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, (0, 0, 0))]))
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4).build([
        Paragraph("ELEMENT NR.4 BLOCKWORK", getSampleStyleSheet()["Heading2"]),
        Spacer(1, 12),
        table,
    ])
    return buffer.getvalue()


def test_supported_extensions_include_word_and_pdf():
    assert ".docx" in SUPPORTED_EXTENSIONS
    assert ".pdf" in SUPPORTED_EXTENSIONS


def test_word_boq_is_parsed_into_elements_and_items():
    parsed = parse_boq_file(_docx_bytes(), "bill.docx")

    assert len(parsed["items"]) == 2
    first = parsed["items"][0]
    assert first["element_name"] == "CONCRETE"
    assert first["description"] == "Concrete 1:3:6 in foundations"
    assert first["quantity"] == 20.0
    assert first["unit"] == "m3"
    assert first["amount"] == 1_100_000.0


def test_word_general_summary_totals_are_read():
    parsed = parse_boq_file(_docx_bytes(), "bill.docx")

    assert parsed["stated"]["sub_total"] == 2_900_000.0
    assert parsed["stated"]["total_contract_sum"] == 2_900_000.0


def test_pdf_boq_table_is_parsed_into_elements_and_items():
    parsed = parse_boq_file(_pdf_bytes(), "bill.pdf")

    assert len(parsed["items"]) >= 2
    assert all(item["element_name"] == "BLOCKWORK" for item in parsed["items"])
    descriptions = " ".join(item["description"] for item in parsed["items"])
    assert "Blockwork" in descriptions
    assert any(item["amount"] for item in parsed["items"])


def test_unreadable_word_reports_a_warning_not_an_exception():
    parsed = parse_boq_file(b"this is not a docx package", "broken.docx")

    assert parsed["items"] == []
    assert any("Could not open the Word document" in w for w in parsed["warnings"])


def test_unreadable_pdf_reports_a_warning_not_an_exception():
    parsed = parse_boq_file(b"%PDF-1.4 not really a pdf", "broken.pdf")

    assert parsed["items"] == []
    assert any("Could not read the PDF" in w for w in parsed["warnings"])
