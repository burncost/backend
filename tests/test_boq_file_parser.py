"""BOQ upload parsing and rate-verification tests.

Two layers:
* `boq_file_parser` is exercised against the real Nigerian QS workbooks in
  `Backend/cads/boq samples/` when present (skipped otherwise, so CI without the
  sample files still passes).
* `boq_generator`'s verification helpers are unit-tested for the guards that stop
  an automatic rate audit from reporting a false "inflated".
"""
import io
from pathlib import Path

import pytest

from app.services.boq_file_parser import (
    SUPPORTED_EXTENSIONS,
    _classify_sheet,
    _element_name,
    _to_number,
    group_items_by_element,
    parse_boq_file,
)

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "cads" / "boq samples"
_SAMPLE_FILES = {
    "airbnb": "BOQ AIR B&B.xls",
    "lecture": "PROPOSED LECTURE HALL.xls",
    "police": "PROPOSED POLICE STATION.xls",
}


def _sample(name: str) -> bytes:
    path = SAMPLES_DIR / _SAMPLE_FILES[name]
    if not path.exists():  # pragma: no cover - samples are optional
        pytest.skip(f"sample BOQ not available: {path}")
    return path.read_bytes()


def _workbook(sheets: dict) -> bytes:
    """Build an in-memory .xlsx from {sheet_name: rows}."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(title=name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# ── helpers ─────────────────────────────────────────────────────────────────

def test_supported_extensions_cover_legacy_xls():
    # The real QS workbooks in the wild are legacy .xls, which openpyxl cannot
    # read at all — xlrd is required.
    assert ".xls" in SUPPORTED_EXTENSIONS
    assert ".xlsx" in SUPPORTED_EXTENSIONS
    assert ".csv" in SUPPORTED_EXTENSIONS


def test_numbers_ignore_page_refs_and_currency_markers():
    assert _to_number(1500.0) == 1500.0
    assert _to_number("1,234.50") == 1234.50
    assert _to_number("N5000") == 5000.0
    assert _to_number("₦ 4,500") == 4500.0
    # "PAGE 2/31" must never be read as a number, nor a bare "N" marker.
    assert _to_number("PAGE 2/31") is None
    assert _to_number("N") is None
    assert _to_number("") is None
    assert _to_number(None) is None
    assert _to_number(True) is None


def test_sheet_roles_and_element_names():
    assert _classify_sheet("ELEMENT NR. 1 SUBSTRUCTURE") == "element"
    assert _classify_sheet("MECHANICA") == "element"
    assert _classify_sheet("SUMMARY") == "summary"
    assert _classify_sheet("GENERAL SUMMARY (2)") == "general_summary"
    assert _classify_sheet("COVER PAGE") == "cover"
    assert _classify_sheet("Sheet3") == "ignore"

    assert _element_name("ELEMENT NR.6 WINDOWS") == "WINDOWS"
    assert _element_name(" ELEMENT NR 7 WALL FINISHES ") == "WALL FINISHES"
    assert _element_name("DRIVES AND PARKING") == "DRIVES AND PARKING"


# ── synthetic workbooks ─────────────────────────────────────────────────────

def test_uppercase_headers_are_parsed():
    """Regression: the old parser only matched lowercase 'description'."""
    content = _workbook({"ELEMENT NR. 1 SUBSTRUCTURE": [
        [None, "DESCRIPTION", "QTY", "UNIT", "RATE", "AMOUNT"],
        ["A", "Excavate oversite 150mm deep", 200, "m3", 410, 82000],
        ["", "Surface treatment;", None, None, None, None],
        ["B", "Anti-termite treatment", 150, "m2", 200, 30000],
        ["", "TO COLLECTION", None, None, None, 112000],
    ]})

    parsed = parse_boq_file(content, "boq.xlsx")

    items = parsed["items"]
    assert [i["description"] for i in items] == [
        "Excavate oversite 150mm deep", "Anti-termite treatment",
    ]
    assert items[0]["ref"] == "A"
    assert items[0]["element_name"] == "SUBSTRUCTURE"
    assert items[0]["unit"] == "m3"
    assert parsed["sheets"][0]["subtotal"] == 112000


def test_sheet_without_header_uses_positional_layout():
    content = _workbook({"ELEMENT NR. 10 FLOOR FINISHES": [
        [None, "ELEMENT NR. 8 FLOOR FINISHES"],
        [],
        ["A", "To receive vitrified ceramic tiles", 1104, "m2", 3980, 4393920],
        ["B", "Skirting; 100mm high", 364, "m", 398, 144872],
    ]})

    parsed = parse_boq_file(content, "boq.xlsx")

    assert len(parsed["items"]) == 2
    assert parsed["items"][0]["quantity"] == 1104
    assert parsed["items"][0]["amount"] == 4393920


def test_carry_forward_rows_are_not_items():
    """'Collection from page N' restates amounts already billed above it."""
    content = _workbook({"ELEMENT NR. 1 SUBSTRUCTURE": [
        [None, "DESCRIPTION", "QTY", "UNIT", "RATE", "AMOUNT"],
        ["A", "Site clearance", 200, "m2", 400, 80000],
        ["", "TO COLLECTION", None, None, None, 80000],
        ["", "COLLECTION"],
        ["", "Collection from page 2/1", None, None, None, 80000],
        ["", "TO SUMMARY", None, None, None, 80000],
    ]})

    parsed = parse_boq_file(content, "boq.xlsx")

    assert len(parsed["items"]) == 1, "carry-forward rows double-book the element"
    assert parsed["sheets"][0]["computed_total"] == 80000
    assert parsed["sheets"][0]["subtotal"] == 80000


def test_provisional_sum_kept_but_sheet_summary_line_skipped():
    content = _workbook({
        "ELEMENT NR. 17 FURNISHING": [
            [None, "ELEMENT NR. 17"],
            ["A", "Allow the provisional Sum of Ten million naira", None, None, None, 10000000],
            ["", "TO SUMMARY", None, None, None, 10000000],
        ],
        "MECHANICA": [
            [None, "Item", "Description", "Unit", "Qty", "Unit cost (NGN)", "Total cost (NGN)"],
            [None, "A", "Supply and install WC suite", "Nr.", 8, 125000, 1000000],
            ["", "Mechanical System Installation", None, None, None, None, 5873970],
        ],
    })

    parsed = parse_boq_file(content, "boq.xlsx")
    by_element = {i["element_name"]: i for i in parsed["items"]}

    # A provisional sum is a real (unpriced) line…
    assert by_element["FURNISHING"]["amount"] == 10000000
    # …while the trailing "Mechanical System Installation" summary line is not.
    assert [i["description"] for i in parsed["items"] if i["element_name"] == "MECHANICA"] == [
        "Supply and install WC suite"
    ]


def test_ditto_expands_from_the_previous_item():
    content = _workbook({"ELEMENT NR. 8 FLOOR FINISH": [
        [None, "DESCRIPTION", "QTY", "UNIT", "RATE", "AMOUNT"],
        ["A", "Floors; screeded bed; 41mm thick", 1104, "m2", 3980, 4393920],
        ["B", "Ditto podium; 300mm wide", 21, "m2", 3980, 83580],
    ]})

    parsed = parse_boq_file(content, "boq.xlsx")

    assert parsed["items"][1]["description"].startswith("Floors; screeded bed; 41mm thick")
    assert "podium" in parsed["items"][1]["description"]


def test_heading_context_is_carried_on_the_item():
    content = _workbook({"ELEMENT NR. 4 INTERNAL&EXT WALL": [
        [None, "DESCRIPTION", "QTY", "UNIT", "RATE", "AMOUNT"],
        ["", "Blockwork"],
        ["", "Blockwork; hollow sandcrete 450x225mm high nominal"],
        ["E", "225mm thick", 650, "m2", 15500, 10075000],
    ]})

    parsed = parse_boq_file(content, "boq.xlsx")

    item = parsed["items"][0]
    assert item["description"] == "225mm thick"
    assert "hollow sandcrete" in item["context"], "rate matching needs the heading"



def test_general_summary_totals_are_read():
    content = _workbook({"GENERAL SUMMARY": [
        [None, "ITEM", "PAGE NR.", "TOTAL (=N=)"],
        [None, "BILL NR 1: PRELIMINARIES", "PAGE 1/20", 4020294.69],
        [None, "ADD CONTINGENCIES:"],
        [None, "ALLOW A PROVISIONAL SUM FOR CONTINGENCY", None, 3000000],
        [None, "SUB-TOTAL", None, 141030117.57],
        [None, "VALUE ADDED TAX (VAT)", 0.075, 10577258.82],
        [None, "TOTAL CONTRACT SUM", None, 151607376.38],
    ]})

    stated = parse_boq_file(content, "boq.xlsx")["stated"]

    assert stated["contingency"] == 3000000
    assert stated["sub_total"] == 141030117.57
    assert stated["vat_rate"] == 0.075
    assert stated["vat"] == 10577258.82
    assert stated["total_contract_sum"] == 151607376.38


def test_unreadable_upload_reports_a_warning_not_an_exception():
    parsed = parse_boq_file(b"not a spreadsheet", "boq.doc")

    assert parsed["items"] == []
    assert parsed["warnings"], "an unsupported upload must explain itself"


def test_group_items_by_element_matches_the_summary_sheet_shape():
    parsed = parse_boq_file(_workbook({"ELEMENT NR. 5 DOORS ": [
        [None, "DESCRIPTION", "QTY", "UNIT", "RATE", "AMOUNT"],
        ["A", "Flush door", 10, "nr", 65000, 650000],
        ["B", "Security door", 2, "nr", 120000, 240000],
    ]}), "boq.xlsx")

    groups = group_items_by_element(parsed["items"])

    assert len(groups) == 1
    assert groups[0]["element_name"] == "DOORS"
    assert groups[0]["element_total"] == 890000
    assert len(groups[0]["items"]) == 2


# ── real sample workbooks ───────────────────────────────────────────────────

def test_real_sample_workbook_is_fully_parsed():
    """The uploaded samples are legacy .xls; every element sheet must be read."""
    parsed = parse_boq_file(_sample("airbnb"), _SAMPLE_FILES["airbnb"])

    assert not parsed["warnings"]
    element_sheets = [s for s in parsed["sheets"] if s["kind"] == "element"]
    assert len(element_sheets) >= 15, "multi-sheet workbooks must not be truncated"
    assert len(parsed["items"]) > 150
    # Blockwork is measured in m² in Nigerian bills (not by block count), and the
    # qualifying heading supplies the material the item line omits.
    blockwork = [
        i for i in parsed["items"]
        if i["element_name"] == "INTERNAL&EXT WALL"
        and i["unit"] == "m2"
        and "hollow sandcrete" in i["context"].lower()
    ]
    assert blockwork, "the blockwork line must be parsed with its heading context"
    assert blockwork[0]["rate"] == 15500
    assert blockwork[0]["quantity"] == 650


def test_real_sample_subtotals_reconcile():
    """Re-adding the parsed items must reproduce each sheet's own subtotal."""
    for name in ("airbnb", "lecture", "police"):
        parsed = parse_boq_file(_sample(name), _SAMPLE_FILES[name])
        mismatched = [
            sheet["element_name"]
            for sheet in parsed["sheets"]
            if sheet["kind"] == "element"
            and sheet["subtotal"] is not None
            and abs(sheet["subtotal"] - sheet["computed_total"]) > 1
        ]
        # One sheet per sample carries a genuine arithmetic slip in the source
        # document; everything else must reconcile to the cent.
        assert len(mismatched) <= 1, f"{name}: {mismatched}"


def test_real_sample_general_summary_is_captured():
    parsed = parse_boq_file(_sample("police"), _SAMPLE_FILES["police"])
    stated = parsed["stated"]

    assert stated["vat_rate"] == 0.075
    assert stated["contingency"] > 0
    assert stated["total_contract_sum"] > 0
    assert stated["element_totals"], "the SUMMARY sheet carries per-element totals"



# ── verification guards (no false 'inflated') ──────────────────────────────

def test_rate_guards_withhold_incomparable_pairs():
    from app.services.boq_generator import _comparable_rate

    item = {"description": "100mm diameter uPVC pipe", "unit": "m", "rate": 4500}
    product = {"rate": 8500, "unit": "length", "product_name": "110mm PVC Pipe"}
    assert _comparable_rate(item, product, item["description"]) == (None, "dimension_mismatch")

    # Same product family, same unit, same size -> a genuine comparison.
    item = {"description": "110mm PVC pipe laid in trench", "unit": "m", "rate": 4000}
    rate, reason = _comparable_rate(item, product, item["description"])
    assert reason is None and rate == 8500

    # A per-drum cable price must not be compared with a per-metre bill rate.
    item = {"description": "2.5mm cable in conduit", "unit": "m", "rate": 2000}
    product = {"rate": 118000, "unit": "drum", "product_name": "2.5mm Electrical Cable"}
    assert _comparable_rate(item, product, item["description"]) == (None, "unit_mismatch")

    # Blockwork: m² of walling against a per-block price converts via 9.9/m².
    item = {"description": "225mm thick", "unit": "m2", "rate": 15500}
    product = {"rate": 1300, "unit": "nr", "product_name": "9-inch Hollow Block"}
    rate, reason = _comparable_rate(item, product, "Blockwork; hollow sandcrete 225mm thick")
    assert reason is None and rate == pytest.approx(1300 * 9.9)

    # A provisional sum has no rate to compare.
    item = {"description": "Allow a provisional sum", "unit": "", "rate": 0}
    assert _comparable_rate(item, product, item["description"]) == (None, "provisional_sum")


def test_verification_summary_counts_and_recommendation():
    from app.services.boq_generator import _verification_summary

    analysis = {
        "city": "Abuja",
        "unverified_count": 1,
        "verified_items": [
            {"description": "over", "quantity": 10, "quoted_rate": 200, "quoted_amount": 2000,
             "unit": "m2", "market_rate": 100, "deviation_pct": 100.0, "status": "inflated",
             "element_name": "WALL FINISHES", "match_reason": "matched"},
            {"description": "under", "quantity": 5, "quoted_rate": 50, "quoted_amount": 250,
             "unit": "nr", "market_rate": 100, "deviation_pct": 50.0, "status": "inflated",
             "element_name": "DOORS", "match_reason": "matched"},
            {"description": "fine", "quantity": 4, "quoted_rate": 100, "quoted_amount": 400,
             "unit": "bag", "market_rate": 100, "deviation_pct": 0.0, "status": "fair",
             "element_name": "SUBSTRUCTURE", "match_reason": "matched"},
            {"description": "none", "quantity": 1, "quoted_rate": 900, "quoted_amount": 900,
             "unit": "nr", "market_rate": None, "deviation_pct": None,
             "status": "unverified", "element_name": "ROOF", "match_reason": "no_match"},
        ],
    }

    summary = _verification_summary(analysis)

    assert summary["total_items_checked"] == 4
    assert summary["overpriced_count"] == 1
    assert summary["underpriced_count"] == 1
    assert summary["fair_count"] == 1
    assert summary["recommendation"] == "REGENERATE"
    assert summary["match_reasons"] == {"matched": 3, "no_match": 1}
    # original 3550 vs adjusted (10x100 + 5x100 + 4x100 + 900) = 2800
    assert summary["original_total"] == 3550
    assert summary["adjusted_total"] == 2800
    assert summary["net_variance"] == -750
    assert summary["flagged_items"][0]["status"] == "overpriced"
    assert summary["flagged_items"][1]["status"] == "underpriced"


def test_postgres_rate_codes_are_keyed_on_the_full_id():
    """Regression: truncated ids collapsed the rate table into one entry."""
    source = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "price_service.py"
    ).read_text(encoding="utf-8")

    assert 'code = f"MAT-{str(r.id)[:8].upper()}"' not in source
    assert 'code = f"MAT-{str(r.id).upper()}"' in source


def test_price_loader_merges_both_stores():
    """Regression: a thin MongoDB collection hid the PostgreSQL rate table."""
    source = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "price_service.py"
    ).read_text(encoding="utf-8")

    assert "merged.setdefault(code, product)" in source, \
        "PostgreSQL rates must be merged in, not skipped when Mongo returns rows"


# ── arithmetic cross-check (item 8) ────────────────────────────────────────

def test_conflicting_general_summaries_are_reported():
    """Revision files carry several summaries; the first wins and a clash warns."""
    declared = [None, "ITEM", "PAGE NR.", "TOTAL (=N=)"]
    content = _workbook({
        "GENERAL SUMMARY": [
            declared,
            [None, "SUB-TOTAL", None, 1000],
            [None, "VALUE ADDED TAX (VAT)", 0.075, 75],
            [None, "TOTAL CONTRACT SUM", None, 1075],
        ],
        "GENERAL SUMMARY (2)": [
            declared,
            [None, "SUB-TOTAL", None, 2000],
            [None, "TOTAL CONTRACT SUM", None, 2150],
        ],
    })

    parsed = parse_boq_file(content, "boq.xlsx")

    assert parsed["stated"]["total_contract_sum"] == 1075, "the first summary is primary"
    assert len(parsed["stated"]["summaries"]) == 2
    assert any("different TOTAL CONTRACT SUM" in w for w in parsed["warnings"])


def test_arithmetic_report_flags_a_sheet_that_does_not_tally():
    from app.services.boq_generator import _arithmetic_report

    sheets = [
        {"kind": "element", "element_name": "ROOF", "item_count": 2,
         "subtotal": 1000.0, "computed_total": 1000.0},
        {"kind": "element", "element_name": "DOORS", "item_count": 3,
         "subtotal": 5000.0, "computed_total": 5240.0},
        {"kind": "summary", "item_count": 0},
    ]

    report = _arithmetic_report(sheets, {})

    assert [e["status"] for e in report["elements"]] == ["ok", "mismatch"]
    assert report["elements"][1]["difference"] == 240.0
    assert report["mismatch_count"] == 1
    assert report["findings"][0]["scope"] == "DOORS"
    assert "240.00" in report["findings"][0]["message"]


def test_arithmetic_report_checks_the_vat_and_contract_chain():
    from app.services.boq_generator import _arithmetic_report

    good = _arithmetic_report([], {"sub_total": 1000.0, "vat_rate": 0.075, "vat": 75.0,
                                   "total_contract_sum": 1075.0})
    assert good["vat"]["matches"] is True
    assert good["contract_sum"]["matches"] is True
    assert good["mismatch_count"] == 0

    bad = _arithmetic_report([], {"sub_total": 1000.0, "vat_rate": 0.075, "vat": 50.0,
                                  "total_contract_sum": 1100.0})
    assert bad["vat"]["matches"] is False
    assert bad["vat"]["expected"] == 75.0
    assert bad["contract_sum"]["matches"] is False
    assert {f["scope"] for f in bad["findings"]} == {"VAT", "TOTAL CONTRACT SUM"}

    # With no stated figures nothing is claimed either way.
    unknown = _arithmetic_report([], {})
    assert unknown["vat"]["matches"] is None
    assert unknown["contract_sum"]["matches"] is None
    assert unknown["mismatch_count"] == 0


def test_real_sample_arithmetic_report_is_accurate():
    """End-to-end: one documented source slip, everything else reconciles."""
    from app.services.boq_generator import _arithmetic_report

    parsed = parse_boq_file(_sample("airbnb"), _SAMPLE_FILES["airbnb"])
    report = _arithmetic_report(parsed["sheets"], parsed["stated"])

    assert report["mismatch_count"] == 1
    assert report["findings"][0]["scope"] == "MECHANICA"
    assert report["vat"]["matches"] is True
    assert report["contract_sum"]["matches"] is True
    assert sum(1 for e in report["elements"] if e["status"] == "ok") == 16


# ── guest masking must not leak the bill (items 8/9) ───────────────────────

def _verification_payload() -> dict:
    """A verification response shaped like `upload_and_verify`'s output."""
    return {
        "boq_id": "6a1b2c3d4e5f60718293a4b5",
        "record_type": "verification",
        "parsed_boq": {
            "items": [
                {"description": f"Item {i}", "quantity": 10, "unit": "m2", "rate": 15500,
                 "amount": 155000}
                for i in range(20)
            ],
            "elements": [{"element_name": "SUBSTRUCTURE", "items": [{"description": "x"}],
                          "element_total": 47449926.0, "item_count": 1}],
            "stated": {
                "sub_total": 395848467.77, "vat": 29688635.08, "vat_rate": 0.075,
                "total_contract_sum": 425537102.85, "contingency": 3500000.0,
                "element_totals": {"SUBSTRUCTURE (All Provisionals)": 47449926.0},
                "bills": {"BILL NR 1: PRELIMINARIES": 22439069.77},
                "summaries": [{"sheet": "GENERAL SUMMARY", "sub_total": 395848467.77,
                               "vat": 29688635.08, "total_contract_sum": 425537102.85,
                               "vat_rate": 0.075,
                               "bills": {"BILL NR 2: POLICE STATION": 369909398.0}}],
            },
            "warnings": [
                "Sheet 'GENERAL SUMMARY (2)' states a different TOTAL CONTRACT SUM "
                "(412972898.60325) than 'GENERAL SUMMARY' (425537102.85275)."
            ],
            "total_quoted": 369933398.0,
        },
        "arithmetic": {
            "elements": [{"element_name": "SUBSTRUCTURE", "item_count": 28,
                          "computed_total": 47449926.0, "stated_total": 47449926.0,
                          "difference": 0.0, "status": "ok"}],
            "items_total": 369933398.0, "stated_elements_total": 369909398.0,
            "items_total_difference": 24000.0,
            "vat": {"rate": 0.075, "stated": 29688635.08, "expected": 29688635.08,
                    "matches": True},
            "contract_sum": {"stated": 425537102.85, "expected": 425537102.85,
                             "matches": True},
            "summaries": [{"sheet": "GENERAL SUMMARY"}],
            "findings": [{"scope": "Electrical", "difference": 24000.0,
                          "message": "Electrical: re-adding the 57 line items gives "
                                     "31,011,700.00 but the sheet totals 30,987,700.00."}],
            "mismatch_count": 1,
        },
        "analysis": {
            "verified_items": [
                {"description": f"Item {i}", "quoted_rate": 15500, "quoted_amount": 155000,
                 "quantity": 10, "unit": "m2", "market_rate": None,
                 "status": "unverified", "match_reason": "no_match"}
                for i in range(20)
            ],
            "discrepancies": [],
            "city": "Abuja",
            "elements": [{"element_name": "SUBSTRUCTURE", "item_count": 28,
                          "computed_total": 47449926.0, "stated_total": 47449926.0,
                          "difference": 0.0}],
            "stated": {"sub_total": 395848467.77, "total_contract_sum": 425537102.85},
            "warnings": ["1 arithmetic issue(s) found in the uploaded BOQ."],
            "verified_count": 0, "unverified_count": 20,
            "original_total": 369933398.0, "adjusted_total": 369933398.0,
            "net_variance": 0.0, "net_variance_pct": 0.0,
            "total_items_checked": 20, "overpriced_count": 0, "underpriced_count": 0,
            "fair_count": 0, "flagged_items": [],
            "recommendation": "REVIEW", "summary_note": "Checked 20 items.",
            "match_reasons": {"no_match": 20},
            "arithmetic": None,
        },
    }


def test_guest_mask_hides_every_stated_figure():
    """Regression: element totals, stated totals, nested bills and warning text."""
    import json

    from app.api.v1.endpoints.boqs import _mask_verification_for_guest

    masked = _mask_verification_for_guest(_verification_payload())
    payload = json.dumps(masked)

    # None of the document's real figures may survive in any copy of them.
    for real in ("47449926", "369909398", "425537102", "395848467", "29688635",
                 "412972898", "22439069"):
        assert real not in payload, f"{real} leaked to the guest payload"

    assert masked["boq_id"] == ""
    assert masked["requires_signup"] is True
    assert len(masked["parsed_boq"]["items"]) == 4, "teaser + locked stub"
    assert all(not e["items"] for e in masked["parsed_boq"]["elements"])
    assert len(masked["analysis"]["verified_items"]) == 3
    assert masked["analysis"]["hidden_items"] == 17
    # The structural shape and reasons stay useful.
    assert masked["parsed_boq"]["stated"]["vat_rate"] == 0.075
    assert masked["analysis"]["match_reasons"] == {"no_match": 20}
    assert masked["arithmetic"]["mismatch_count"] == 1
    assert "Electrical" in masked["arithmetic"]["findings"][0]["message"]

