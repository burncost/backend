"""Read an uploaded PDF's real metadata, tables and first-page image.

This module previously returned *invented* data — a hard-coded ``pageCount`` of
5, an "Architect Name" author and a two-line "bill of quantities" table — which
was then handed to the AI analysis as though it had been read from the user's
file. Nothing here is guessed: every value comes from the document, and a file
that cannot be read reports an ``error`` instead of a plausible-looking lie.

The returned shape is deliberately unchanged (``pageCount``, ``author``,
``subject``, ``keywords``, ``tables[]``) so existing callers keep working; no
top-level ``detectedType`` is set, because the BOQ extraction path keys off that
flag and these tables have not been through the bill parser.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Extracting a page's tables walks its whole object graph, so only the first
# pages pay for it — the page *count* reported is always the document's real one.
_MAX_TABLE_PAGES = 50
_MAX_TABLE_ROWS = 200
_TEXT_PREVIEW_CHARS = 2000
_DEFAULT_THUMBNAIL_DPI = 110

# A table whose header row names these columns is a priced bill.
_BILL_HEADER_WORDS = ("description", "qty", "quantity", "rate", "amount", "unit")


def _clean_cell(value: Any) -> str:
    """Flatten a pdfplumber cell (str or None) into single-line text."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _detect_table_type(rows: List[List[str]]) -> str:
    """Label a table by its header row: 'bill_of_quantities' or 'table'."""
    if not rows:
        return "table"
    header = " ".join(cell.lower() for cell in rows[0])
    hits = sum(1 for word in _BILL_HEADER_WORDS if word in header)
    return "bill_of_quantities" if hits >= 2 else "table"


class PDFExtractor:
    """Reads PDFs with pdfplumber (content) and PyMuPDF (rendering)."""

    ### Extract metadata and content from PDF
    async def extract(self, file_content: bytes) -> Dict[str, Any]:
        """Return the document's real metadata and tables.

        Blocking work runs in a thread so a large PDF never stalls the event
        loop while it is parsed.
        """
        logger.info("Extracting PDF metadata")
        return await asyncio.to_thread(self._extract_sync, file_content)

    ### Generate thumbnail from PDF first page
    async def generate_thumbnail(self, file_content: bytes) -> Optional[bytes]:
        """Render page 1 to PNG bytes, or None when it cannot be rendered."""
        logger.info("Generating PDF thumbnail")
        return await asyncio.to_thread(self._thumbnail_sync, file_content)

    # ── blocking implementations ─────────────────────────────────────────────

    def _extract_sync(self, file_content: bytes) -> Dict[str, Any]:
        try:
            import pdfplumber
        except ImportError:  # pragma: no cover - dependency is declared
            return self._error("Reading PDF files requires the 'pdfplumber' package.")

        result: Dict[str, Any] = {
            "pageCount": 0,
            "title": None,
            "author": None,
            "subject": None,
            "keywords": [],
            "creator": None,
            "producer": None,
            "tables": [],
            "textPreview": "",
        }

        try:
            with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                result["pageCount"] = len(pdf.pages)
                self._apply_metadata(result, pdf.metadata or {})
                self._collect_tables(result, pdf.pages)
                if pdf.pages:
                    text = pdf.pages[0].extract_text() or ""
                    result["textPreview"] = text[:_TEXT_PREVIEW_CHARS]
        except Exception as exc:  # noqa: BLE001 - never raise on user input
            logger.warning(f"PDF extraction failed: {exc}")
            return self._error(f"Could not read the PDF: {exc}")

        return result

    @staticmethod
    def _error(message: str) -> Dict[str, Any]:
        """An empty, clearly-failed result — never fabricated content."""
        return {
            "pageCount": 0,
            "title": None,
            "author": None,
            "subject": None,
            "keywords": [],
            "tables": [],
            "textPreview": "",
            "errors": [message],
        }

    @staticmethod
    def _apply_metadata(result: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        """Copy the document's own title/author/subject/keywords across."""
        result["title"] = metadata.get("Title") or None
        result["author"] = metadata.get("Author") or None
        result["subject"] = metadata.get("Subject") or None
        result["creator"] = metadata.get("Creator") or None
        result["producer"] = metadata.get("Producer") or None
        raw_keywords = metadata.get("Keywords") or ""
        result["keywords"] = [
            keyword.strip() for keyword in str(raw_keywords).split(",") if keyword.strip()
        ]

    def _collect_tables(self, result: Dict[str, Any], pages: List[Any]) -> None:
        """Append each page's ruled tables (bounded) to `tables`."""
        for number, page in enumerate(pages[:_MAX_TABLE_PAGES], start=1):
            try:
                found = page.extract_tables() or []
            except Exception as exc:  # noqa: BLE001 - one bad page is not fatal
                logger.debug(f"Table extraction failed on page {number}: {exc}")
                continue
            for table in found:
                rows = [
                    [_clean_cell(cell) for cell in row]
                    for row in (table or [])[:_MAX_TABLE_ROWS]
                ]
                rows = [row for row in rows if any(row)]
                if len(rows) < 2:
                    # A single row is a header or a stray line, not a table.
                    continue
                result["tables"].append({
                    "pageNumber": number,
                    "tableData": rows,
                    "detectedType": _detect_table_type(rows),
                })

    def _thumbnail_sync(self, file_content: bytes) -> Optional[bytes]:
        try:
            import fitz  # PyMuPDF
        except ImportError:  # pragma: no cover - dependency is declared
            logger.info("PyMuPDF not installed; skipping PDF thumbnail")
            return None
        try:
            with fitz.open(stream=file_content, filetype="pdf") as document:
                if document.page_count == 0:
                    return None
                page = document.load_page(0)
                pixmap = page.get_pixmap(dpi=_DEFAULT_THUMBNAIL_DPI)
                return pixmap.tobytes("png")
        except Exception as exc:  # noqa: BLE001 - never raise on user input
            logger.warning(f"PDF thumbnail generation failed: {exc}")
            return None
