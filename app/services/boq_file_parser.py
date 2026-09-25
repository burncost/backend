"""Parse an uploaded Bill of Quantities (Excel/CSV) into element-grouped items.

Real Nigerian QS BOQs are multi-sheet workbooks: one sheet per SMM7 element
("ELEMENT NR. 1 SUBSTRUCTURE", "ELEMENT NR.6 WINDOWS", ...), wrapped by a COVER
PAGE, a SUMMARY (per-element totals) and a GENERAL SUMMARY (bill total,
contingencies, VAT, contract sum). Inside an element sheet the layout is
positional:

    [0] item ref letter (A, B, C ...)   [1] DESCRIPTION   [2] QTY
    [3] UNIT   [4] RATE   [5] AMOUNT    ([6]/[7] stray values -> ignored)

Blank rows separate items, description-only rows are section headings, and
"TO COLLECTION" / "TO SUMMARY" / "TO GENERAL SUMMARY" rows carry the element
subtotal. Legacy .xls (BIFF8) is read with xlrd, .xlsx/.xlsm with openpyxl;
neither reader is assumed present (a warning is returned instead of raising).

Pure and synchronous by design: no DB, no async, so it is cheap to unit test.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = (".xls", ".xlsx", ".xlsm", ".csv")

# Sheet roles. Element sheets hold the measurable items; the rest are wrappers.
_SUMMARY_SHEET_RE = re.compile(r"^summary\b", re.I)
_GENERAL_SUMMARY_SHEET_RE = re.compile(r"^general\s+summary", re.I)
_COVER_SHEET_RE = re.compile(r"^cover(\s+page)?\b", re.I)
_IGNORED_SHEET_RE = re.compile(r"^sheet\d*$", re.I)
_ELEMENT_PREFIX_RE = re.compile(r"^\s*element\s*(nr\.?|no\.?|number)?\s*\d*\s*", re.I)

# Rows that carry a total rather than a measurable item. "Collection from page
# N" is the per-page carry-forward block at the foot of an element sheet — it
# re-states amounts already itemised above, so counting it double-books the
# element (it must never be parsed as an item).
_SUBTOTAL_MARKERS = (
    "to collection", "collection from", "collection brought",
    "to summary", "to general summary",
    "sub-total", "subtotal", "sub total",
    "carried forward", "brought forward",
)

# Header synonyms, matched case-insensitively against normalised cell text.
_HEADER_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "ref": ("item ref", "itemref", "ref", "s/n", "sn", "no.", "item no", "item no."),
    "description": ("description", "item description", "desc"),
    "quantity": ("qty", "quantity", "qnty"),
    "unit": ("unit", "uom", "units"),
    "rate": ("rate", "unit rate", "unit cost"),
    "amount": ("amount", "total", "value"),
}

# A cell is numeric only when it is a number or looks like a plain number — this
# keeps "PAGE 2/31" and the "N" currency marker out of the totals.
_PLAIN_NUMBER_RE = re.compile(r"^\s*[₦nN]?\s*-?\d[\d,]*(?:\.\d+)?\s*$")
_HEADER_JUNK_RE = re.compile(r"[^a-z0-9/\.\s]")
_DITTO_RE = re.compile(r"^\s*ditto\b[\s:;,\-]*", re.I)
_WS_RE = re.compile(r"\s+")

# Column positions used when a sheet carries no header row (one sample's
# "ELEMENT NR. 8 FLOOR FINISHES" sheet starts straight into the items).
_POSITIONAL_COLUMNS = {
    "ref": 0, "description": 1, "quantity": 2, "unit": 3, "rate": 4, "amount": 5,
}



# ── value helpers ────────────────────────────────────────────────────────────

def _to_number(value: Any) -> Optional[float]:
    """Parse a spreadsheet cell into a float, or None when it is not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text or not _PLAIN_NUMBER_RE.match(text):
        return None
    cleaned = text.replace(",", "").replace("₦", "").strip()
    cleaned = re.sub(r"^[nN]\s*", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _WS_RE.sub(" ", str(value)).strip()


def _extension(filename: str) -> str:
    name = (filename or "").strip().lower()
    return ("." + name.rsplit(".", 1)[-1]) if "." in name else ""


def _cell(row: List[Any], index: Optional[int]) -> Any:
    if index is None or index < 0 or index >= len(row):
        return None
    return row[index]


# ── sheet roles ──────────────────────────────────────────────────────────────

def _classify_sheet(name: str) -> str:
    """Return "element" | "summary" | "general_summary" | "cover" | "ignore"."""
    text = (name or "").strip()
    if _GENERAL_SUMMARY_SHEET_RE.match(text):
        return "general_summary"
    if _SUMMARY_SHEET_RE.match(text):
        return "summary"
    if _COVER_SHEET_RE.match(text):
        return "cover"
    if _IGNORED_SHEET_RE.match(text):
        return "ignore"
    return "element"


def _element_name(sheet_name: str) -> str:
    """Sheet name minus its prefix: "ELEMENT NR.6 WINDOWS" -> "WINDOWS"."""
    name = _ELEMENT_PREFIX_RE.sub("", sheet_name or "").strip()
    return name or (sheet_name or "").strip()


# ── column detection ─────────────────────────────────────────────────────────

def _normalise_header(value: Any) -> str:
    text = _as_text(value).lower()
    text = text.replace("₦", " ").replace("(n)", " ").replace("(₦)", " ")
    text = text.replace("(", " ").replace(")", " ")
    text = _HEADER_JUNK_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _match_header(text: str) -> Optional[str]:
    if not text:
        return None
    for field, synonyms in _HEADER_SYNONYMS.items():
        for synonym in synonyms:
            if text == synonym or text.startswith(synonym):
                return field
    return None


def _detect_header(rows: List[List[Any]]) -> Tuple[Optional[Dict[str, int]], Optional[int]]:
    """Find the header row and map fields to column indexes.

    Returns (columns, header_row_index), or (None, None) when the sheet has no
    header row at all.
    """
    for index, row in enumerate(rows[:12]):
        mapping: Dict[str, int] = {}
        for column, cell in enumerate(row):
            field = _match_header(_normalise_header(cell))
            if field and field not in mapping:
                mapping[field] = column
        if len(mapping) >= 2 and ("description" in mapping or "amount" in mapping):
            # The samples label DESCRIPTION..AMOUNT and leave the item-ref column
            # unlabelled: it sits immediately to the left of DESCRIPTION.
            if "ref" not in mapping and mapping.get("description", 0) > 0:
                mapping["ref"] = mapping["description"] - 1
            return mapping, index
    return None, None


def _positional_columns(rows: List[List[Any]]) -> Optional[Dict[str, int]]:
    """Fall back to the standard layout, validated against real data rows.

    A provisional-sum sheet (e.g. FURNITURE, which carries an amount but no
    quantity or rate) uses the same columns, so validation accepts a rate *or*
    an amount; section headings have neither and so cannot validate a sheet.
    """
    for row in rows:
        if len(row) < 6:
            continue
        description = _as_text(row[1])
        rate = _to_number(row[4])
        amount = _to_number(row[5])
        if description and (rate is not None or amount is not None):
            return dict(_POSITIONAL_COLUMNS)
    return None


# Descriptions that legitimately carry an amount without a quantity or rate.
_PROVISIONAL_SUM_RE = re.compile(r"provisional|allow\b|allowance", re.I)


# ── row classification ───────────────────────────────────────────────────────

def _is_subtotal_label(text: str) -> bool:
    low = _as_text(text).lower()
    return any(marker in low for marker in _SUBTOTAL_MARKERS)


def _expand_ditto(text: str, previous: Optional[str]) -> str:
    """Expand QS shorthand: "Ditto podium ;300mm wide" -> previous + the clause."""
    if not previous:
        return text
    match = _DITTO_RE.match(text or "")
    if not match:
        return text
    remainder = (text or "")[match.end():].strip(" ;,-")
    return f"{previous} {remainder}".strip() if remainder else previous


def _clean_description(text: str) -> str:
    """Collapse whitespace and trim clause punctuation, keeping the wording."""
    cleaned = _WS_RE.sub(" ", (text or "").replace("\n", " ")).strip()
    return cleaned.strip(" .;:-")


# ── sheet reading ────────────────────────────────────────────────────────────

def _read_sheets(
    content: bytes, filename: str
) -> Tuple[List[Tuple[str, List[List[Any]]]], List[str]]:
    """Return [(sheet_name, rows)] plus non-fatal warnings."""
    extension = _extension(filename)
    warnings: List[str] = []

    if extension == ".csv":
        import csv
        import io

        text = content.decode("utf-8-sig", errors="replace")
        return [("CSV", [list(r) for r in csv.reader(io.StringIO(text))])], warnings

    if extension == ".xls":
        try:
            import xlrd
        except ImportError:  # pragma: no cover - dependency is declared
            return [], ["Reading legacy .xls BOQ files requires the 'xlrd' package."]
        book = xlrd.open_workbook(file_contents=content)
        sheets = [
            (
                sheet.name,
                [[sheet.cell_value(r, c) for c in range(sheet.ncols)]
                 for r in range(sheet.nrows)],
            )
            for sheet in book.sheets()
        ]
        return sheets, warnings

    if extension in (".xlsx", ".xlsm"):
        try:
            import io

            import openpyxl
        except ImportError:  # pragma: no cover - dependency is declared
            return [], ["Reading .xlsx BOQ files requires the 'openpyxl' package."]
        book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheets = [
            (ws.title, [list(row) for row in ws.iter_rows(values_only=True)])
            for ws in book.worksheets
        ]
        return sheets, warnings

    return [], [
        f"Unsupported file format: {extension or 'unknown'}. Upload CSV, .xls or .xlsx."
    ]



# ── element sheet parsing ────────────────────────────────────────────────────

def _parse_element_sheet(
    rows: List[List[Any]],
    columns: Dict[str, int],
    element_name: str,
    sheet_name: str,
    start_row: int = 0,
) -> Tuple[List[Dict[str, Any]], Optional[float], List[str]]:
    """Parse one element sheet into items, its subtotal and any warnings."""
    items: List[Dict[str, Any]] = []
    subtotal: Optional[float] = None
    warnings: List[str] = []
    previous_description: Optional[str] = None
    # Qualified headings above an item carry the material and size — the samples
    # list "225mm thick" under a "Blockwork; hollow sandcrete 450x225mm" heading,
    # and a rate match needs that wording.
    heading_window: List[str] = []

    for offset, row in enumerate(rows[start_row:], start=start_row):
        raw_description = _as_text(_cell(row, columns.get("description")))
        if not raw_description:
            continue

        quantity = _to_number(_cell(row, columns.get("quantity")))
        rate = _to_number(_cell(row, columns.get("rate")))
        amount = _to_number(_cell(row, columns.get("amount")))
        ref = _as_text(_cell(row, columns.get("ref")))
        unit = _as_text(_cell(row, columns.get("unit")))

        if _is_subtotal_label(raw_description):
            captured = amount if amount is not None else rate
            if captured is None:
                # Some sheets place the value in an unlabelled trailing column.
                numbers = [n for n in (_to_number(c) for c in row) if n is not None]
                captured = max(numbers) if numbers else None
            if captured is not None:
                subtotal = captured
            continue

        # A description with no measurement at all is a section heading.
        if quantity is None and rate is None and amount is None:
            heading = _clean_description(raw_description)
            if heading and (not heading_window or heading_window[-1] != heading):
                heading_window.append(heading)
                del heading_window[:-3]
            continue

        # An amount with neither quantity nor rate is either a provisional sum
        # (a real priced item) or the sheet's own summary line —
        # e.g. MECHANICA ends with a block of "Sanitary Appliances 1,604,000" /
        # "Mechanical System Installation 5,873,970" rows that re-state totals
        # already itemised above. Counting those would inflate the element.
        if quantity is None and rate is None and amount is not None:
            if not _PROVISIONAL_SUM_RE.search(raw_description):
                subtotal = amount
                continue

        description = _clean_description(_expand_ditto(raw_description, previous_description))
        previous_description = description

        flags: List[str] = []
        if amount is None and quantity is not None and rate is not None:
            amount = round(quantity * rate, 2)
            flags.append("amount_calculated")
        elif amount is not None and quantity is not None and rate is not None:
            if abs(amount - quantity * rate) > max(1.0, 0.01 * abs(amount)):
                flags.append("amount_mismatch")
        if quantity is None:
            flags.append("quantity_missing")

        items.append({
            "element_name": element_name,
            "sheet": sheet_name,
            "row": offset + 1,
            "ref": ref,
            "description": description,
            "description_raw": raw_description,
            "context": "; ".join(heading_window),
            "quantity": quantity,
            "unit": unit,
            "rate": rate,
            "amount": amount,
            "flags": flags,
        })

    if not items:
        warnings.append(f"No priced items found on sheet '{sheet_name}'.")
    return items, subtotal, warnings


# ── summary sheets ───────────────────────────────────────────────────────────

def _read_summary_sheet(
    rows: List[List[Any]], include_element_totals: bool
) -> Dict[str, Any]:
    """Read element totals (SUMMARY) or the bill/contingency/VAT block (GENERAL SUMMARY).

    Values are taken from the first numeric cell *after* the row's label — real
    sheets carry a second, unrelated figure column (wider scope / another
    currency) to the right, so ``max(cells)`` picks up junk such as
    16,800,000,000 for a ₦480,000 fittings line.
    """
    stated: Dict[str, Any] = {}
    element_totals: Dict[str, float] = {}

    for row in rows:
        label = ""
        label_index = -1
        for column, cell in enumerate(row):
            text = _as_text(cell)
            if text and _to_number(cell) is None:
                label = text
                label_index = column
                break
        if not label:
            continue

        numbers: List[float] = []
        for cell in row[label_index + 1:]:
            number = _to_number(cell)
            if number is not None:
                numbers.append(number)
        if not numbers:
            continue

        low = label.lower()
        amounts = [n for n in numbers if abs(n) >= 1]
        rates = [n for n in numbers if 0 < n < 1]
        value = amounts[0] if amounts else numbers[0]

        if include_element_totals:
            element_totals[label] = value
            continue
        if "contingenc" in low:
            stated["contingency"] = value
        elif "value added tax" in low or low.startswith("vat"):
            stated["vat"] = value
            if rates:
                stated["vat_rate"] = rates[0]
        elif "total contract sum" in low:
            stated["total_contract_sum"] = value
        elif "sub-total" in low or "sub total" in low:
            stated["sub_total"] = value
        elif low.startswith("bill nr") or "block of" in low:
            stated.setdefault("bills", {})[label or "Bill"] = value

    if include_element_totals and element_totals:
        stated["element_totals"] = element_totals
    return stated



# ── public entry point ───────────────────────────────────────────────────────

def parse_boq_file(content: bytes, filename: str) -> Dict[str, Any]:
    """Parse a BOQ upload into element-grouped items plus the document's stated totals.

    Never raises for malformed content: the caller decides how to report
    `warnings` and an empty `items` list.
    """
    sheets, warnings = _read_sheets(content, filename)
    if not sheets:
        return {"sheets": [], "items": [], "stated": {}, "warnings": warnings}

    items: List[Dict[str, Any]] = []
    sheet_reports: List[Dict[str, Any]] = []
    stated: Dict[str, Any] = {}
    element_sheets = 0

    for name, rows in sheets:
        kind = _classify_sheet(name)
        if kind in ("cover", "ignore"):
            continue
        if kind in ("summary", "general_summary"):
            found = _read_summary_sheet(rows, include_element_totals=(kind == "summary"))
            if kind == "summary":
                if found.get("element_totals"):
                    stated.setdefault("element_totals", {}).update(found["element_totals"])
            else:
                # Revision files carry more than one GENERAL SUMMARY. The first is
                # treated as primary (a later sheet must not silently overwrite the
                # stated contract sum) and any disagreement is reported.
                summaries = stated.setdefault("summaries", [])
                summaries.append({"sheet": name, **found})
                if len(summaries) == 1:
                    stated.update(found)
                elif found.get("total_contract_sum") != stated.get("total_contract_sum"):
                    warnings.append(
                        f"Sheet '{name}' states a different TOTAL CONTRACT SUM "
                        f"({found.get('total_contract_sum')}) than "
                        f"'{summaries[0]['sheet']}' ({stated.get('total_contract_sum')})."
                    )
            sheet_reports.append({"name": name, "kind": kind, "item_count": 0})
            continue

        element_sheets += 1
        element_name = _element_name(name)
        columns, header_row = _detect_header(rows)
        start_row = (header_row + 1) if header_row is not None else 0
        if columns is None:
            columns = _positional_columns(rows)
            start_row = 0
        if columns is None:
            warnings.append(
                f"Could not find a description/qty/rate layout on sheet '{name}'."
            )
            sheet_reports.append({"name": name, "kind": kind, "item_count": 0})
            continue

        sheet_items, subtotal, sheet_warnings = _parse_element_sheet(
            rows, columns, element_name, name, start_row=start_row
        )
        warnings.extend(sheet_warnings)
        items.extend(sheet_items)
        sheet_reports.append({
            "name": name,
            "kind": kind,
            "element_name": element_name,
            "item_count": len(sheet_items),
            "subtotal": subtotal,
            "computed_total": round(amount_sum(sheet_items), 2),
        })

    if element_sheets and not items:
        warnings.append("No line items could be parsed from this BOQ.")
    if not stated.get("total_contract_sum") and stated.get("element_totals"):
        warnings.append("No TOTAL CONTRACT SUM row was found in the GENERAL SUMMARY.")

    return {
        "sheets": sheet_reports,
        "items": items,
        "stated": stated,
        "warnings": warnings,
    }


def amount_sum(items: List[Dict[str, Any]]) -> float:
    """Sum item amounts, tolerating missing/blank amounts."""
    return sum(float(i.get("amount") or 0.0) for i in items)


def group_items_by_element(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group parsed items into the BOQ element shape used by the app's schema.

    Yields [{element_name, items: [...], element_total}] preserving sheet order,
    which mirrors the SUMMARY sheet of a real BOQ.
    """
    grouped: List[Dict[str, Any]] = []
    index: Dict[str, Dict[str, Any]] = {}
    for item in items or []:
        name = item.get("element_name") or "Unclassified"
        group = index.get(name)
        if group is None:
            group = {"element_name": name, "items": [], "element_total": 0.0, "item_count": 0}
            index[name] = group
            grouped.append(group)
        group["items"].append(item)
        group["item_count"] = len(group["items"])
        group["element_total"] = round(group["element_total"] + float(item.get("amount") or 0.0), 2)
    return grouped

