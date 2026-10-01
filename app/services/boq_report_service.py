"""Branded BOQ report renderers (PDF + Word).

Reuses the PyMuPDF layout primitives already written for the watermarked vendor
report (`vendor_report_service`) so a BOQ export carries the same BurnCost
look: diagonal BURNCOST watermark, orange accent rules, a project title block
and a footer with "Page X of Y". No new dependency is introduced - PyMuPDF and
python-docx are both already required by the export path.

Money is written with an ASCII "NGN" prefix and every string is folded to
Latin-1 before drawing, because the base-14 PDF fonts carry neither the naira
sign (U+20A6) nor other non-Western glyphs and would otherwise raise (base-14)
or be silently dropped - the bug that left the old `reportlab` PDF looking empty.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Iterable, Optional

import fitz  # PyMuPDF

from app.services import vendor_report_service as _vrs

WATERMARK_TEXT = _vrs.WATERMARK_TEXT
BOLD_FONT = _vrs._BOLD  # Helvetica-Bold, for the total banner
MARGIN = _vrs.MARGIN
A4_W = _vrs.A4_W
BRAND = _vrs.BRAND


def _ascii(text: Any) -> str:
    """Fold a value to Latin-1 so the base-14 PDF fonts can always draw it.

    The naira sign becomes "NGN " (base-14 fonts have no U+20A6 glyph); dashes
    are normalised and anything else outside Latin-1 becomes "?" rather than
    raising mid-render.
    """
    value = "" if text is None else str(text)
    value = (
        value.replace("\u20a6", "NGN ")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    return value.encode("latin-1", "replace").decode("latin-1")


def money(value: Any) -> str:
    """Amount as an ASCII-safe 'NGN 1,234.56' (mirrors the vendor report)."""
    try:
        return f"NGN {float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "NGN 0.00"


def _qty(value: Any) -> str:
    """Compact quantity: no trailing zeros, thousands separated."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return _ascii(value)
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _pct(value: Any, default: float) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return str(int(number)) if number == int(number) else f"{number:g}"


def _pick(mapping: dict, *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return 0


def _summary_rows(summary: dict) -> list[tuple[str, Any]]:
    """The deduction chain in Nigerian QS order (sub-total -> contingency ->
    overheads & profit -> VAT). Tolerates both summary shapes the pipeline
    writes (`contingency`/`contingency_amount`, `vat`/`vat_amount`, ...)."""
    s = summary or {}
    return [
        ("Sub Total", _pick(s, "sub_total", "subTotal")),
        (f"Contingency ({_pct(s.get('contingency_pct'), 10)}%)", _pick(s, "contingency_amount", "contingency")),
        (f"Overheads & Profit ({_pct(s.get('overheads_profit_pct'), 10)}%)",
         _pick(s, "overheads_profit_amount", "overheads_profit")),
        (f"VAT ({_pct(s.get('vat_pct'), 7.5)}%)", _pick(s, "vat_amount", "vat")),
    ]


def _project_meta(project_info: dict, generated_at: datetime) -> list[tuple[str, str]]:
    info = project_info or {}
    return [
        ("Client", _ascii(_pick(info, "client", "client_name") or "-")),
        ("Project Ref", _ascii(_pick(info, "reference", "project_ref", "ref") or "-")),
        ("Date", _ascii(_pick(info, "date", "project_date") or generated_at.strftime("%Y-%m-%d"))),
        ("Location", _ascii(_pick(info, "location", "city") or "-")),
    ]


class _BOQReport(_vrs._Report):
    """The vendor report frame plus an orange TOTAL CONTRACT SUM banner.

    Everything else - watermark, orange header rule, repeating table headers,
    footer and page numbers - is inherited unchanged from `_Report`.
    """

    def total_banner(self, label: str, value: Any) -> None:
        height = 26.0
        self._ensure(height + 10)
        self.y += 4
        rect = fitz.Rect(MARGIN, self.y, A4_W - MARGIN, self.y + height)
        self.page.draw_rect(rect, color=None, fill=BRAND)
        self.page.insert_text(
            fitz.Point(MARGIN + 8, self.y + 17), _ascii(label),
            fontsize=12, fontname=_vrs.HEAD_FONT, color=(1, 1, 1),
        )
        amount = money(value)
        start = A4_W - MARGIN - 8 - BOLD_FONT.text_length(amount, 12)
        self.page.insert_text(
            fitz.Point(start, self.y + 17), amount,
            fontsize=12, fontname=_vrs.HEAD_FONT, color=(1, 1, 1),
        )
        self.y += height + 8


_BOQ_COLUMNS = ["Item", "Description", "Qty", "Unit", "Rate (NGN)", "Amount (NGN)"]


def build_boq_pdf(
    *,
    project_title: str,
    elements: Optional[Iterable[dict]] = None,
    summary: Optional[dict] = None,
    project_info: Optional[dict] = None,
    generated_at: Optional[datetime] = None,
) -> bytes:
    """Render a branded, watermarked BOQ and return the PDF bytes.

    Always non-empty: the project title block and the summary chain are drawn
    even when there are no measurable items, so a download can never be blank.
    """
    generated_at = generated_at or datetime.utcnow()
    title = _ascii(project_title or "Bill of Quantities")
    report = _BOQReport(
        title="BILL OF QUANTITIES (BOQ)",
        subtitle=(
            f"{title}  |  Prepared to NIQS / BESMM3 measurement standards  |  "
            f"{generated_at.strftime('%Y-%m-%d')}"
        ),
        footer_note=(
            f"BurnCost - {title} - Confidential - {generated_at.strftime('%Y-%m-%d')}"
        ),
    )
    report.kpis(_project_meta(project_info or {}, generated_at))

    element_list = list(elements or [])
    if not element_list:
        report.note("No measurable items were carried on this bill.")
    for el in element_list:
        name = str(el.get("elementName") or el.get("element_name") or "Element")
        report.heading(_ascii(name.upper()))
        rows: list[list[str]] = []
        running = 0.0
        for item in el.get("items") or []:
            qty = item.get("quantity") or 0
            rate = item.get("adjusted_rate") or item.get("rate") or 0
            amount = item.get("amount")
            if amount in (None, ""):
                try:
                    amount = float(qty) * float(rate)
                except (TypeError, ValueError):
                    amount = 0
            running += float(amount or 0)
            rows.append([
                _ascii(item.get("item_code") or item.get("itemCode") or ""),
                _ascii(item.get("description") or ""),
                _qty(qty),
                _ascii(item.get("unit") or ""),
                money(rate),
                money(amount),
            ])
        if rows:
            report.table(_BOQ_COLUMNS, rows)
        report.note(f"Element total: {money(running)}")

    data = summary or {}
    report.heading("SUMMARY")
    report.table(
        ["Item", "Amount (NGN)"],
        [[label, money(amount)] for label, amount in _summary_rows(data)],
    )
    report.total_banner("TOTAL CONTRACT SUM", data.get("total_contract_sum"))
    return report.finish()


# ── Word (.docx) rendering ───────────────────────────────────────────────────
# python-docx has no native watermark/pagination API, so the diagonal BURNCOST
# text is injected as a VML shape into the section header and the page numbers
# as field codes in the footer - the same XML Word itself writes for those.

_ORANGE_HEX = "FF6B00"
_HEADER_HEX = "C05621"


def _shade(element, hex_fill: str) -> None:
    """Apply a solid background fill to a paragraph or table cell (w:shd)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    target = element._p.get_or_add_pPr() if hasattr(element, "_p") else element._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    target.append(shd)


def _cell(cell, value: Any, *, bold: bool = False, size: int = 9,
          color: Optional[str] = None, shade: Optional[str] = None) -> None:
    """Replace a cell's text with one styled run (and optional fill)."""
    from docx.shared import Pt, RGBColor

    cell.text = ""
    run = cell.paragraphs[0].add_run("" if value is None else str(value))
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    if shade:
        _shade(cell, shade)


def _field(paragraph, instruction: str) -> None:
    """Insert a Word field code (PAGE, NUMPAGES, ...) into a paragraph."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


def _docx_watermark(section, text: str = WATERMARK_TEXT) -> None:
    """Faint diagonal watermark in the section header (VML textpath, rotation 315)."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    header = section.header
    header.is_linked_to_previous = False
    paragraph = header.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    xml = (
        f'<w:pict {nsdecls("w")} xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office">'
        '<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800" '
        'path="m@7,l@8,m@5,21600l@6,21600e"><v:formulas>'
        '<v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/>'
        '<v:f eqn="sum 0 0 @2"/><v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/>'
        '<v:f eqn="if @0 21600 @1"/><v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/>'
        '<v:f eqn="mid @5 @6"/><v:f eqn="mid @8 @5"/><v:f eqn="mid @7 @8"/>'
        '<v:f eqn="mid @6 @7"/><v:f eqn="sum @6 0 @5"/></v:formulas>'
        '<v:path textpathok="t" o:connecttype="custom" '
        'o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" o:connectangles="270,180,90,0"/>'
        '<v:textpath on="t" fitshape="t"/><v:handles>'
        '<v:h position="#0,bottomRight" xrange="6629,14971"/></v:handles></v:shapetype>'
        '<v:shape id="BurnCostWatermark" o:spid="_x0000_s2049" type="#_x0000_t136" '
        'style="position:absolute;margin-left:0;margin-top:0;width:412.4pt;height:200pt;'
        'rotation:315;z-index:-251658752;mso-position-horizontal:center;'
        'mso-position-horizontal-relative:margin;mso-position-vertical:center;'
        'mso-position-vertical-relative:margin" o:allowincell="f" '
        'fillcolor="#D9D9D9" stroked="f">'
        f'<v:textpath style="font-family:&quot;Calibri&quot;;font-size:1pt" string="{text}"/>'
        '</v:shape></w:pict>'
    )
    run._r.append(parse_xml(xml))


def _docx_footer(section) -> None:
    """Footer rule with page numbers: 'BurnCost - Confidential BOQ   Page X of Y'."""
    from docx.shared import Pt, RGBColor

    footer = section.footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    lead = paragraph.add_run("BurnCost - Confidential BOQ        Page ")
    lead.font.size = Pt(8)
    lead.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    _field(paragraph, "PAGE")
    middle = paragraph.add_run(" of ")
    middle.font.size = Pt(8)
    middle.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    _field(paragraph, "NUMPAGES")



def build_boq_docx(
    *,
    project_title: str,
    elements: Optional[Iterable[dict]] = None,
    summary: Optional[dict] = None,
    project_info: Optional[dict] = None,
    generated_at: Optional[datetime] = None,
) -> bytes:
    """Render a branded, watermarked Word BOQ and return the .docx bytes.

    Like the PDF builder this is always non-empty: the title block and the
    summary chain (with the orange TOTAL CONTRACT SUM row) render even with no
    measurable items.
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    generated_at = generated_at or datetime.utcnow()
    title_text = str(project_title or "Bill of Quantities")
    document = Document()

    for section in document.sections:
        section.top_margin = Cm(1.6)
        section.bottom_margin = Cm(1.6)
        section.left_margin = Cm(1.6)
        section.right_margin = Cm(1.6)

    section = document.sections[0]
    _docx_watermark(section)
    _docx_footer(section)

    def centered(text: str, *, size: int, bold: bool = False, italic: bool = False,
                 color: Optional[str] = None):
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(text)
        run.bold = bold
        run.italic = italic
        run.font.size = Pt(size)
        if color:
            run.font.color.rgb = RGBColor.from_string(color)
        return paragraph

    centered("BURNCOST", size=11, bold=True, color=_ORANGE_HEX)
    centered("BILL OF QUANTITIES (BOQ)", size=20, bold=True)
    centered(title_text, size=12)
    meta = "  |  ".join(
        f"{label}: {value}" for label, value in _project_meta(project_info or {}, generated_at)
    )
    centered(f"{meta}  |  Currency: Nigerian Naira (NGN)", size=9, color="555555")
    centered("Prepared to NIQS / BESMM3 measurement standards", size=9, italic=True, color="555555")

    element_list = list(elements or [])
    if not element_list:
        centered("No measurable items were carried on this bill.", size=9, italic=True, color="777777")
    for el in element_list:
        name = str(el.get("elementName") or el.get("element_name") or "Element")
        heading = document.add_paragraph()
        _shade(heading, _ORANGE_HEX)
        head_run = heading.add_run(f"  {name.upper()}")
        head_run.bold = True
        head_run.font.size = Pt(11)
        head_run.font.color.rgb = RGBColor.from_string("FFFFFF")

        table = document.add_table(rows=1, cols=len(_BOQ_COLUMNS))
        table.style = "Table Grid"
        for cell, label in zip(table.rows[0].cells, _BOQ_COLUMNS):
            _cell(cell, label, bold=True, size=9, color="FFFFFF", shade=_HEADER_HEX)

        running = 0.0
        for item in el.get("items") or []:
            qty = item.get("quantity") or 0
            rate = item.get("adjusted_rate") or item.get("rate") or 0
            amount = item.get("amount")
            if amount in (None, ""):
                try:
                    amount = float(qty) * float(rate)
                except (TypeError, ValueError):
                    amount = 0
            running += float(amount or 0)
            cells = table.add_row().cells
            _cell(cells[0], item.get("item_code") or item.get("itemCode") or "")
            _cell(cells[1], item.get("description") or "")
            _cell(cells[2], _qty(qty))
            _cell(cells[3], item.get("unit") or "")
            _cell(cells[4], money(rate))
            _cell(cells[5], money(amount))

        subtotal = document.add_paragraph()
        subtotal.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        subtotal_run = subtotal.add_run(f"Element total: {money(running)}")
        subtotal_run.bold = True
        subtotal_run.font.size = Pt(9)

    data = summary or {}
    summary_heading = document.add_paragraph()
    _shade(summary_heading, _ORANGE_HEX)
    summary_run = summary_heading.add_run("  SUMMARY")
    summary_run.bold = True
    summary_run.font.color.rgb = RGBColor.from_string("FFFFFF")

    summary_table = document.add_table(rows=0, cols=2)
    summary_table.style = "Table Grid"
    for label, amount in _summary_rows(data):
        cells = summary_table.add_row().cells
        _cell(cells[0], label, bold=True)
        _cell(cells[1], money(amount))

    total_cells = summary_table.add_row().cells
    _cell(total_cells[0], "TOTAL CONTRACT SUM", bold=True, size=12, color="FFFFFF", shade=_ORANGE_HEX)
    _cell(total_cells[1], money(data.get("total_contract_sum")), bold=True, size=12,
          color="FFFFFF", shade=_ORANGE_HEX)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()

