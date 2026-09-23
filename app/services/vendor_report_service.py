"""Vendor performance report (PDF) renderer.

Built with PyMuPDF, which is already a project dependency - no new packages required.

Layout is drawn with the base-14 PDF fonts (helv/hebo) instead of MuPDF's HTML engine.
That keeps every string searchable and copyable (the HTML engine substitutes ligature
glyphs, so "In-flight" extracts as "In-fl<ligature>"), paginates predictably, and needs
no separate measuring pass. Money uses an ASCII "NGN" prefix because the base-14 fonts
carry no naira glyph (U+20A6), which would render as a tofu box.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Optional

import fitz  # PyMuPDF

WATERMARK_TEXT = "BURNCOST"
WATERMARK_GREY = (0.86, 0.86, 0.86)  # light so table text stays readable
FOOTER_GREY = (0.45, 0.45, 0.45)
BRAND = (1.0, 0.42, 0.0)  # BurnCost orange
INK = (0.08, 0.09, 0.11)
HEAD_FILL = (0.945, 0.965, 0.98)
ROW_LINE = (0.886, 0.910, 0.941)

A4_W, A4_H = 595.0, 842.0
MARGIN = 40.0
CONTENT_W = A4_W - 2 * MARGIN
HEADER_H = 58.0
FOOTER_H = 30.0
ROW_H = 15.5
KPI_ROW_H = 32.0
FONT_SIZE = 8.5
HEAD_SIZE = 8.5
HEAD_FONT = "hebo"  # Helvetica-Bold
BODY_FONT = "helv"  # Helvetica

_BOLD = fitz.Font(HEAD_FONT)
_REGULAR = fitz.Font(BODY_FONT)


def money(value: Any) -> str:
    """Format an amount as ASCII-safe 'NGN 1,234.56'."""
    try:
        return f"NGN {float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "NGN 0.00"


def _clip(text: Any, width: float, font: fitz.Font, size: float) -> str:
    """Truncate to fit a column (base-14 fonts do not wrap inside a drawn cell)."""
    value = "" if text is None else str(text)
    if font.text_length(value, size) <= width:
        return value
    while value and font.text_length(value + "...", size) > width:
        value = value[:-1]
    return value + "..."


def _column_widths(columns: list[str], rows: list[list[Any]]) -> list[float]:
    """Proportional widths from the longest content per column (so money stays narrow)."""
    weights = []
    for index, column in enumerate(columns):
        longest = _BOLD.text_length(str(column), HEAD_SIZE)
        for row in rows[:60]:
            if index < len(row):
                longest = max(longest, _REGULAR.text_length(str(row[index]), FONT_SIZE))
        weights.append(min(max(longest + 14, 46.0), 210.0))
    total = sum(weights) or 1.0
    return [weight * CONTENT_W / total for weight in weights]


def _right_aligned(columns: list[str], rows: list[list[Any]]) -> set[int]:
    """Columns whose cells are all numeric/amounts get right-aligned (report convention)."""
    aligned: set[int] = set()
    for index in range(len(columns)):
        cells = [str(row[index]) for row in rows[:60] if index < len(row) and str(row[index]).strip()]
        if cells and all(
            cell.startswith("NGN ") or cell.rstrip("%").replace(",", "").replace(".", "").isdigit()
            for cell in cells
        ):
            aligned.add(index)
    return aligned


class _Report:
    """Composes A4 pages: header on creation, content blocks, then footer + page numbers."""

    def __init__(self, title: str, subtitle: str, footer_note: str):
        self.doc = fitz.open()
        self.title = title
        self.subtitle = subtitle
        self.footer_note = footer_note
        self.page: fitz.Page = None
        self.y = 0.0
        self._new_page()

    # -- page frame ---------------------------------------------------------
    @property
    def _bottom(self) -> float:
        return A4_H - MARGIN - FOOTER_H

    def _new_page(self):
        self.page = self.doc.new_page(width=A4_W, height=A4_H)
        # Watermark first, so the page content is drawn on top of it and stays legible.
        writer = fitz.TextWriter(self.page.rect)
        writer.append((150, 520), WATERMARK_TEXT, font=_BOLD, fontsize=64)
        writer.write_text(
            self.page, color=WATERMARK_GREY, morph=(fitz.Point(298, 420), fitz.Matrix(45)),
        )
        # insert_text() with an explicit baseline: insert_textbox() silently draws
        # NOTHING when the rect is a pixel short, which dropped whole sections.
        self.page.insert_text(
            fitz.Point(MARGIN, MARGIN - 2), self.title,
            fontsize=15, fontname=HEAD_FONT, color=INK,
        )
        self.page.insert_text(
            fitz.Point(MARGIN, MARGIN + 16), self.subtitle,
            fontsize=9, fontname=BODY_FONT, color=FOOTER_GREY,
        )
        self.page.draw_line(
            fitz.Point(MARGIN, MARGIN + 28), fitz.Point(A4_W - MARGIN, MARGIN + 28),
            color=BRAND, width=1.4,
        )
        self.y = MARGIN + HEADER_H

    def _ensure(self, height: float):
        if self.y + height > self._bottom:
            self._new_page()

    def _cell(self, x: float, width: float, y: float, text: Any,
              size: float, font: fitz.Font, fontname: str, right: bool = False,
              color=INK, fontsize_pad: float = 4.0):
        value = _clip(text, width - 2 * fontsize_pad, font, size)
        start = x + width - fontsize_pad - font.text_length(value, size) if right else x + fontsize_pad
        self.page.insert_text(fitz.Point(start, y), value, fontsize=size, fontname=fontname, color=color)

    # -- blocks -------------------------------------------------------------
    def heading(self, text: str, space_before: float = 14.0):
        self._ensure(space_before + 20)
        self.y += space_before
        self.page.insert_text(
            fitz.Point(MARGIN, self.y + 12), text,
            fontsize=11, fontname=HEAD_FONT, color=BRAND,
        )
        self.y += 19

    def note(self, text: str):
        """Small grey caption (wraps when long)."""
        self._ensure(26)
        self.page.insert_textbox(
            fitz.Rect(MARGIN, self.y, A4_W - MARGIN, self.y + 26),
            str(text), fontsize=8, fontname=BODY_FONT, color=FOOTER_GREY,
        )
        self.y += 26

    def kpis(self, pairs: list[tuple[str, str]]):
        """Two label/value pairs per row."""
        column_w = CONTENT_W / 2
        for index in range(0, len(pairs), 2):
            if self.y + KPI_ROW_H > self._bottom:
                self._new_page()
            for offset, (label, value) in enumerate(pairs[index:index + 2]):
                x = MARGIN + offset * column_w
                self._cell(x, column_w, self.y + 9, label, 7.5, _REGULAR, BODY_FONT, color=FOOTER_GREY)
                self._cell(x, column_w, self.y + 25, value, 11, _BOLD, HEAD_FONT)
            self.y += KPI_ROW_H

    def table(self, columns: list[str], rows: list[list[Any]]):
        """Header + rows, repeating the header whenever a new page is started."""
        widths = _column_widths(columns, rows)
        right = _right_aligned(columns, rows)

        def draw_header():
            self.page.draw_rect(
                fitz.Rect(MARGIN, self.y, A4_W - MARGIN, self.y + ROW_H), color=None, fill=HEAD_FILL,
            )
            x = MARGIN
            for column, width in zip(columns, widths):
                self._cell(x, width, self.y + 11, column, HEAD_SIZE, _BOLD, HEAD_FONT)
                x += width
            self.y += ROW_H

        self._ensure(ROW_H * 2)
        draw_header()
        for row in rows:
            if self.y + ROW_H > self._bottom:
                self._new_page()
                draw_header()
            x = MARGIN
            for index, (cell, width) in enumerate(zip(row, widths)):
                self._cell(x, width, self.y + 11, cell, FONT_SIZE, _REGULAR, BODY_FONT,
                           right=index in right)
                x += width
            self.page.draw_line(
                fitz.Point(MARGIN, self.y + ROW_H), fitz.Point(A4_W - MARGIN, self.y + ROW_H),
                color=ROW_LINE, width=0.5,
            )
            self.y += ROW_H
        self.y += 6

    def add_section(self, section: dict):
        columns = section.get("columns") or []
        rows = section.get("rows") or []
        self.heading(section["heading"])
        if section.get("meta"):
            self.kpis(section["meta"])
        if columns:
            if rows:
                self.table(columns, rows)
            else:
                self.note(section.get("empty", "No data"))
        if section.get("note"):
            self.note(section["note"])

    def finish(self) -> bytes:
        total = self.doc.page_count
        for index, page in enumerate(self.doc, start=1):
            footer_y = A4_H - MARGIN - 20
            page.draw_line(
                fitz.Point(MARGIN, footer_y), fitz.Point(A4_W - MARGIN, footer_y),
                color=ROW_LINE, width=0.7,
            )
            page.insert_text(
                fitz.Point(MARGIN, footer_y + 12), self.footer_note,
                fontsize=8, fontname=BODY_FONT, color=FOOTER_GREY,
            )
            page_number = f"Page {index} of {total}"
            page.insert_text(
                fitz.Point(A4_W - MARGIN - _REGULAR.text_length(page_number, 8), footer_y + 12),
                page_number, fontsize=8, fontname=BODY_FONT, color=FOOTER_GREY,
            )
        out = io.BytesIO()
        self.doc.save(out)
        self.doc.close()
        return out.getvalue()


def build_vendor_report_pdf(
    *,
    vendor_name: str,
    period_label: str,
    summary_meta: list[tuple[str, str]],
    sections: list[dict],
    generated_at: Optional[datetime] = None,
) -> bytes:
    """Render the vendor report and return the PDF bytes."""
    generated_at = generated_at or datetime.utcnow()
    report = _Report(
        title=f"BurnCost Vendor Report - {vendor_name}",
        subtitle=f"{period_label}  |  generated {generated_at.strftime('%Y-%m-%d %H:%M')} UTC",
        footer_note=(
            f"Generated by BurnCost - Confidential - {vendor_name} - "
            f"{generated_at.strftime('%Y-%m-%d')}"
        ),
    )
    report.heading("Summary", space_before=6)
    report.kpis(summary_meta)
    for section in sections:
        report.add_section(section)
    return report.finish()
