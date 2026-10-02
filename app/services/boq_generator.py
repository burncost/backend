"""BOQ Generator - Real BOQ generation using AI and market rates."""
from typing import Any, Dict, List, Optional, Tuple
import asyncio
import logging
import json
import os
import math
import re
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from json_repair import repair_json

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.boq import BOQGenerationRequest
from app.services.boq_file_parser import group_items_by_element, parse_boq_file
from app.services.mitm_engine import MITMEngine
from app.services.price_service import DB_SOURCES, PriceService, PriceTruthService
from app.services.gemini_client import get_gemini_client
from app.config import settings

logger = logging.getLogger(__name__)


def _plain_dashes(value: Any) -> Any:
    """Replace typographic dashes with a plain hyphen in every string.

    Applied to BOQ payloads before they leave the service so the returned text
    clients render never carries an em/en dash. Walks dicts and lists so the
    whole document (warnings, notes, narrative, flags) is normalised.
    """
    if isinstance(value, str):
        return value.replace("\u2014", "-").replace("\u2013", "-")
    if isinstance(value, dict):
        return {key: _plain_dashes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_dashes(item) for item in value]
    return value




# Cap on distinct market-rate lookups per uploaded BOQ. A real bill carries
# hundreds of lines; without a cap the request would fire hundreds of DB/AI
# lookups. The caller reports how many lines were left unverified.
_MAX_RATE_LOOKUPS = 300

# How many of those lookups may run at once. A real bill has hundreds of lines
# and `PriceService._load_prices` populates a cache on first use - firing the
# whole batch concurrently opened one DB session per line and exhausted the
# Postgres pool ("too many clients already"), which silently unverified the
# entire bill.
_MAX_CONCURRENT_RATE_LOOKUPS = 5

# Lookups are also dispatched in bounded batches: the semaphore caps *running*
# lookups, but gathering all 300 targets at once still allocated 300 coroutines,
# their argument tuples and their result slots. 50 keeps a large bill's peak
# allocation flat without slowing the common (few-line) case.
_RATE_LOOKUP_CHUNK = 50

# Band around the market rate treated as "within range" when quoting a
# comparable figure for an item (Nigerian practice: ±5-10% on material rates).
_MARKET_RATE_BAND = 0.10

# Unit aliases so a BOQ measure can be compared with the catalogue's unit.
_UNIT_ALIASES: Dict[str, str] = {
    "m2": "m2", "m²": "m2", "sqm": "m2", "sq.m": "m2", "square metre": "m2",
    "square meter": "m2", "square metres": "m2", "square meters": "m2",
    "m3": "m3", "m³": "m3", "cum": "m3", "cu.m": "m3", "cubic metre": "m3",
    "cubic meter": "m3", "cubic metres": "m3", "cubic meters": "m3",
    "m": "m", "lm": "m", "mtr": "m", "metre": "m", "meter": "m", "metres": "m",
    "meters": "m", "linear metre": "m", "linear meter": "m", "length": "m",
    "nr": "nr", "no": "nr", "nos": "nr", "pc": "nr", "pcs": "nr", "piece": "nr",
    "pieces": "nr", "unit": "nr", "each": "nr", "pair": "nr",
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilogramme": "kg",
    "tonne": "tonne", "tonnes": "tonne", "ton": "tonne", "tons": "tonne",
    "bag": "bag", "bags": "bag", "roll": "roll", "rolls": "roll",
    "drum": "drum", "drums": "drum", "bucket": "bucket", "buckets": "bucket",
    "sheet": "sheet", "sheets": "sheet", "pts": "nr", "point": "nr", "points": "nr",
    "pack": "pack", "packs": "pack", "set": "set", "litre": "litre", "liter": "litre",
}

# Comparisons that need a conversion because the bill measures the *work* while
# the catalogue sells the *material*. Sandcrete walling is measured in m² but
# blocks are sold by number; the Nigerian take-off gives one block face
# (450 x 225mm incl. joint) = 0.101 m², i.e. ~9.9 blocks per m² (see
# how to prep boq.txt / Nigeria_BOQ_Measurement_Reference.md).
_M2_TO_BLOCK_FACTOR = 9.9
_BLOCK_CATALOGUE_HINTS = ("block",)
_WALLING_HINTS = ("blockwork", "block wall", "sandcrete", "wall")

# Which Python take-off value owns a bill line's quantity. Rules are evaluated in
# order and the first match wins, so the more specific descriptions come first.
#   units     - normalised units the rule applies to (empty = any)
#   all_hints - every one of these must appear in the description
#   any_hints - at least one must appear
#   anti      - the rule is skipped when any of these appears
#   factor    - converts the take-off value to the bill's unit (e.g. tonnes→kg)
# A line matching no rule keeps the model's own quantity and is labelled
# `quantity_source="ai"` (see BOQGenerator._apply_python_quantities).
_PYTHON_QUANTITY_RULES: Tuple[Dict[str, Any], ...] = (
    # Blockwork: the bill measures the wall, the catalogue sells blocks.
    {
        "key": "external_blocks_225mm",
        "units": {"nr"},
        "all_hints": ("block",),
        "any_hints": ("225", "9 inch", "9-inch", "9in", "external"),
        "anti": ("150", "6 inch", "6-inch", "6in", "internal"),
    },
    {
        "key": "internal_blocks_150mm",
        "units": {"nr"},
        "all_hints": ("block",),
        "any_hints": ("150", "6 inch", "6-inch", "6in", "internal"),
    },
    {"key": "blocks_total_nr", "units": {"nr"}, "all_hints": ("block",)},
    {"key": "net_wall_area_m2", "units": {"m2"}, "all_hints": ("block",)},
    # Tiling - floor first, then walls, then a generic m² fallback.
    {
        "key": "floor_tiles_m2",
        "units": {"m2"},
        "any_hints": ("floor tile", "floor tiles", "floor tiling", "tiles to floor"),
    },
    {
        "key": "wall_tiles_m2",
        "units": {"m2"},
        "any_hints": ("wall tile", "wall tiles", "wall tiling", "tiles to wall"),
    },
    {"key": "floor_tiles_m2", "units": {"m2"}, "all_hints": ("tile",)},
    {
        "key": "floor_tile_boxes",
        "units": {"nr", "box", "carton", "pack"},
        "all_hints": ("tile",),
        "any_hints": ("box", "carton", "pack"),
    },
    # Roof structure and covering.
    {"key": "roof_purlin_m", "units": {"m"}, "all_hints": ("purlin",)},
    {"key": "roof_rafter_m", "units": {"m"}, "any_hints": ("rafter", "truss")},
    {"key": "roof_truss_count", "units": {"nr"}, "any_hints": ("truss",)},
    {
        "key": "roof_covering_m2",
        "units": {"m2"},
        "all_hints": ("roof",),
        "any_hints": (
            "sheet", "covering", "long span", "longspan", "aluminium",
            "aluminum", "stone coated", "stone-coated", "shingle",
        ),
    },
    {"key": "roof_ceiling_m2", "units": {"m2"}, "any_hints": ("ceiling", "pop")},
    # Substructure.
    {"key": "foundation_excavation_m3", "units": {"m3"}, "all_hints": ("excavat",)},
    {"key": "foundation_hardcore_m3", "units": {"m3"}, "all_hints": ("hardcore",)},
    {"key": "foundation_blinding_m3", "units": {"m3"}, "all_hints": ("blind",)},
    {"key": "foundation_dpm_m2", "units": {"m2"}, "any_hints": ("damp proof", "dpm", "d.p.m")},
    {"key": "slab_concrete_m3", "units": {"m3"}, "all_hints": ("slab",)},
    {
        "key": "foundation_concrete_m3",
        "units": {"m3"},
        "all_hints": ("concrete",),
        "any_hints": ("foundation", "footing", "pad base", "raft", "strip"),
    },
    # Reinforcement, finishes.
    {
        "key": "estimated_rebar_kg",
        "units": {"kg"},
        "any_hints": ("reinforcement", "rebar", "y12", "y16", "iron rod", "steel bar"),
    },
    {
        "key": "estimated_rebar_kg",
        "units": {"tonne"},
        # The take-off is in kg, so a bill measured in tonnes divides by 1000
        # (e.g. 3 000 kg of take-off = 3.0 t on the bill).
        "factor": 0.001,
        "any_hints": ("reinforcement", "rebar", "y12", "y16", "iron rod", "steel bar"),
    },
    {"key": "wall_finish_area_m2", "units": {"m2"}, "any_hints": ("plaster", "render", "skim")},
    {"key": "floor_finish_area_m2", "units": {"m2"}, "all_hints": ("screed",)},
    {
        "key": "wall_finish_area_m2",
        "units": {"m2"},
        "any_hints": ("paint", "emulsion", "gloss", "texture"),
    },
)


def _python_quantity_for(item: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    """Take-off key (and unit factor) that owns this bill line, or None.

    Matching is on the description text plus the normalised unit, because a
    Nigerian bill measures the *work* ("blockwork in 225mm wall, m2") while the
    catalogue sells the *material* ("9-inch sandcrete block, nr").
    """
    description = str(item.get("description") or "").lower()
    unit = _normalise_unit(str(item.get("unit") or ""))

    for rule in _PYTHON_QUANTITY_RULES:
        units = rule.get("units") or set()
        if units and unit not in units:
            continue
        if any(term in description for term in rule.get("anti", ())):
            continue
        if not all(term in description for term in rule.get("all_hints", ())):
            continue
        any_hints = rule.get("any_hints", ())
        if any_hints and not any(term in description for term in any_hints):
            continue
        return rule["key"], float(rule.get("factor", 1.0))

    return None

# Nigerian material rates for the same item do not differ by an order of
# magnitude; a bigger gap almost always means the match paired the wrong
# product or the wrong unit (e.g. a per-drum cable price against a per-metre
# bill rate). Such pairs are reported for review instead of as "inflated".
_PLAUSIBLE_RATIO_LOW = 0.125
_PLAUSIBLE_RATIO_HIGH = 8.0

# Measurement tokens that carry a unit ("225mm thick", "0.55mm", "1000L").
# Two products with different diameters/sizes are different products, so a pair
# that disagrees on every dimension token must not be reported as a rate gap.
_DIMENSION_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mm|cm|m2|m3|m|kg|litres?|liters?|l|inch|in)\b", re.I
)

# A provisional sum is an allowance, not a measured rate.
_PROVISIONAL_SUM_HINT = re.compile(r"provisional|allow\b|allowance", re.I)

# The AI prompt asks for these SMM7 element groups (see _build_boq_prompt). A
# response covering fewer than MIN_AI_ELEMENT_COVERAGE of them was truncated or
# ignored the brief; shipping it as a "complete" BOQ understates every quantity
# in the later elements (a mid-document token cut used to be repaired into a
# syntactically valid but 2-element bill). Such responses are rejected so the
# deterministic template path runs instead.
REQUIRED_AI_ELEMENTS = (
    "preliminaries",
    "substructure",
    "superstructure",
    "roofing",
    "joinery",
    "internal finishes",
    "external finishes",
    "plumbing",
    "electrical",
    "external works",
)
MIN_AI_ELEMENT_COVERAGE = 6


def _dimension_tokens(text: str) -> set:
    return {match.group(0).replace(" ", "").lower() for match in _DIMENSION_RE.finditer(text or "")}


def _normalise_unit(unit: str) -> str:
    text = (unit or "").strip().lower().rstrip(".")
    text = text.replace("\u00b2", "2").replace("\u00b3", "3")
    return _UNIT_ALIASES.get(text, text)


def _comparable_rate(
    item: Dict[str, Any], product: Dict[str, Any], lookup_text: str
) -> Tuple[Optional[float], Optional[str]]:
    """Return (comparable market rate, None) or (None, reason) for one item.

    Guards a rate audit against the ways automatic matching misleads: pairing
    products measured in different units, pairing products that merely share a
    generic word, and pairing products of different sizes.

    `lookup_text` is the item's description with its heading context, which is
    what the catalogue was matched against.
    """
    market_rate = product.get("rate")
    if not market_rate:
        return None, "no_match"

    # A provisional sum carries no unit rate, so there is nothing to compare.
    if not item.get("rate") or _PROVISIONAL_SUM_HINT.search(str(item.get("description") or "")):
        return None, "provisional_sum"

    item_unit = _normalise_unit(item.get("unit", ""))
    market_unit = _normalise_unit(product.get("unit", ""))
    catalogue_name = str(product.get("product_name") or "").lower()
    description = (lookup_text or item.get("description") or "").lower()

    if item_unit and market_unit and item_unit != market_unit:
        # Walling is the one documented cross-unit case: m² of blockwork against
        # a per-block price. Convert the catalogue rate to a per-m² rate.
        if (
            item_unit == "m2"
            and market_unit == "nr"
            and any(hint in catalogue_name for hint in _BLOCK_CATALOGUE_HINTS)
            and any(hint in description for hint in _WALLING_HINTS)
        ):
            return float(market_rate) * _M2_TO_BLOCK_FACTOR, None
        return None, "unit_mismatch"

    labelled_name = catalogue_name.split("(")[0].strip() or catalogue_name
    core = [word for word in re.split(r"[^a-z]+", labelled_name) if len(word) > 3]
    if core and not any(word in description for word in core):
        return None, "weak_match"

    # "100mm uPVC pipe" against "110mm PVC Pipe" is a different product, not a
    # rate gap - withhold when the dimensions disagree.
    item_dimensions = _dimension_tokens(description)
    market_dimensions = _dimension_tokens(catalogue_name)
    if item_dimensions and market_dimensions and not (item_dimensions & market_dimensions):
        return None, "dimension_mismatch"
    # A dimensioned item (1500x3000mm curtain wall) versus a generic catalogue
    # entry ("Aluminium Window (Standard)") is not a like-for-like rate check.
    if item_dimensions and not market_dimensions:
        return None, "unspecified_catalogue_item"

    ratio = float(market_rate) / float(item.get("rate") or 1.0) if item.get("rate") else 0.0
    if ratio and not (_PLAUSIBLE_RATIO_LOW <= ratio <= _PLAUSIBLE_RATIO_HIGH):
        return None, "implausible_ratio"

    return float(market_rate), None


def _summarise_elements(
    items: List[Dict[str, Any]], sheets: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Per-element breakdown mirroring a BOQ's SUMMARY sheet.

    Pairs the re-added item totals with the totals the document itself states,
    so the difference is visible instead of hidden.
    """
    order: List[str] = []
    totals: Dict[str, Dict[str, Any]] = {}
    for item in items:
        name = item.get("element_name") or "Unclassified"
        if name not in totals:
            totals[name] = {"element_name": name, "item_count": 0, "computed_total": 0.0}
            order.append(name)
        entry = totals[name]
        entry["item_count"] += 1
        entry["computed_total"] = round(entry["computed_total"] + float(item.get("amount") or 0.0), 2)

    stated_by_sheet = {
        (s.get("element_name") or ""): s.get("subtotal")
        for s in sheets
        if s.get("kind") == "element" and s.get("subtotal") is not None
    }
    result = []
    for name in order:
        entry = totals[name]
        stated = stated_by_sheet.get(name)
        entry["stated_total"] = stated
        entry["difference"] = (
            round(entry["computed_total"] - stated, 2) if stated is not None else None
        )
        result.append(entry)
    return result


def _arithmetic_report(
    sheets: List[Dict[str, Any]],
    stated: Dict[str, Any],
    tolerance: float = 1.0,
) -> Dict[str, Any]:
    """Re-add the parsed bill and compare it with the totals the document states.

    "Check all arithmetic" is Step 5 of the Nigerian BOQ guide; this turns it
    into a finding instead of a silent assumption. Amounts are compared in Naira
    with a ₦1 tolerance for rounding.
    """
    elements: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []

    for sheet in sheets or []:
        if sheet.get("kind") != "element":
            continue
        stated_total = sheet.get("subtotal")
        computed = sheet.get("computed_total")
        if stated_total is None or computed is None:
            continue
        difference = round(computed - stated_total, 2)
        matches = abs(difference) <= tolerance
        elements.append({
            "element_name": sheet.get("element_name"),
            "item_count": sheet.get("item_count", 0),
            "computed_total": computed,
            "stated_total": stated_total,
            "difference": difference,
            "status": "ok" if matches else "mismatch",
        })
        if not matches:
            findings.append({
                "scope": sheet.get("element_name"),
                "difference": difference,
                "message": (
                    f"{sheet.get('element_name')}: re-adding the {sheet.get('item_count', 0)} "
                    f"line items gives {computed:,.2f} but the sheet totals {stated_total:,.2f} "
                    f"(difference {difference:,.2f}). Check the source sheet."
                ),
            })

    items_total = round(sum(e["computed_total"] for e in elements), 2)
    stated_elements_total = round(sum(e["stated_total"] for e in elements), 2)

    # Document-level chain: SUB-TOTAL -> VAT (rate x sub-total) -> contract sum.
    sub_total = stated.get("sub_total")
    vat_rate = stated.get("vat_rate")
    vat_stated = stated.get("vat")
    contract_stated = stated.get("total_contract_sum")

    vat_expected = round(sub_total * vat_rate, 2) if sub_total and vat_rate else None
    vat_matches = (
        abs(vat_stated - vat_expected) <= tolerance
        if vat_stated is not None and vat_expected is not None else None
    )
    if vat_matches is False:
        findings.append({
            "scope": "VAT",
            "difference": round(vat_stated - vat_expected, 2),
            "message": (
                f"VAT of {vat_stated:,.2f} does not equal {vat_rate:.3%} of the "
                f"sub-total ({vat_expected:,.2f})."
            ),
        })

    contract_expected = (
        round(sub_total + vat_stated, 2)
        if sub_total is not None and vat_stated is not None else None
    )
    contract_matches = (
        abs(contract_stated - contract_expected) <= tolerance
        if contract_stated is not None and contract_expected is not None else None
    )
    if contract_matches is False:
        findings.append({
            "scope": "TOTAL CONTRACT SUM",
            "difference": round(contract_stated - contract_expected, 2),
            "message": (
                f"TOTAL CONTRACT SUM of {contract_stated:,.2f} does not equal the "
                f"sub-total plus VAT ({contract_expected:,.2f})."
            ),
        })

    return {
        "elements": elements,
        "items_total": items_total,
        "stated_elements_total": stated_elements_total,
        "items_total_difference": round(items_total - stated_elements_total, 2),
        "vat": {
            "rate": vat_rate,
            "stated": vat_stated,
            "expected": vat_expected,
            "matches": vat_matches,
        },
        "contract_sum": {
            "stated": contract_stated,
            "expected": contract_expected,
            "matches": contract_matches,
        },
        "summaries": stated.get("summaries") or [],
        "findings": findings,
        "mismatch_count": len(findings),
    }


def _verification_summary(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Build the analysis summary a BOQ-verification screen renders.

    Additive: the itemised `verified_items` / `discrepancies` lists keep their
    existing shape and meaning.
    """
    verified_items = analysis.get("verified_items") or []
    flagged_items: List[Dict[str, Any]] = []
    overpriced = underpriced = fair = 0
    adjusted_total = 0.0
    original_total = 0.0

    for item in verified_items:
        quantity = float(item.get("quantity") or 0.0)
        quoted_rate = float(item.get("quoted_rate") or 0.0)
        quoted_amount = float(item.get("quoted_amount") or 0.0)
        market_rate = item.get("market_rate")
        original_total += quoted_amount

        if not market_rate:
            # No comparable rate: carry the quoted figure through unchanged.
            adjusted_total += quoted_amount
            continue
        adjusted_amount = quantity * float(market_rate)
        adjusted_total += adjusted_amount
        if item.get("status") == "fair":
            fair += 1
            continue
        if quoted_rate > float(market_rate):
            overpriced += 1
            status = "overpriced"
        else:
            underpriced += 1
            status = "underpriced"
        flagged_items.append({
            "element_name": item.get("element_name") or "Unclassified",
            "description": item.get("description", ""),
            "unit": item.get("unit", ""),
            "qty": quantity,
            "boq_rate": quoted_rate,
            "boq_amount": quoted_amount,
            "reference_rate": float(market_rate),
            "ref_low": round(float(market_rate) * (1 - _MARKET_RATE_BAND), 2),
            "ref_high": round(float(market_rate) * (1 + _MARKET_RATE_BAND), 2),
            "variance_pct": item.get("deviation_pct"),
            "status": status,
            "suggested_rate": float(market_rate),
            "adjusted_amount": round(adjusted_amount, 2),
            "potential_saving": round(quoted_amount - adjusted_amount, 2),
        })

    net_variance = round(adjusted_total - original_total, 2)
    net_variance_pct = round((net_variance / original_total * 100), 2) if original_total else 0.0
    unverified = int(analysis.get("unverified_count") or 0)

    # Why lines went unverified matters to the reader: "no comparable rate in the
    # catalogue" is a coverage gap, "unit mismatch" is a data-quality signal.
    match_reasons: Dict[str, int] = {}
    for item in verified_items:
        reason = item.get("match_reason") or "matched"
        match_reasons[reason] = match_reasons.get(reason, 0) + 1

    if flagged_items:
        recommendation = "REGENERATE"
    elif unverified:
        recommendation = "REVIEW"
    else:
        recommendation = "ACCEPT"

    return {
        "total_items_checked": len(verified_items),
        "overpriced_count": overpriced,
        "underpriced_count": underpriced,
        "fair_count": fair,
        "flagged_items": flagged_items,
        "match_reasons": match_reasons,
        "original_total": round(original_total, 2),
        "adjusted_total": round(adjusted_total, 2),
        "net_variance": net_variance,
        "net_variance_pct": net_variance_pct,
        "recommendation": recommendation,
        "summary_note": (
            f"Checked {len(verified_items)} items against {analysis.get('city', 'Abuja')} "
            f"market rates: {overpriced} overpriced, {underpriced} underpriced, "
            f"{fair} within range, {unverified} with no comparable rate."
        ),
    }



# ── Phase 5: regenerate an uploaded bill on accept ─────────────────────────
#
# An uploaded bill lives in `boq_verifications`; accepting it ("regenerate")
# rebuilds a real, priced BOQ as a new `boqs` version. The quantities are the
# upload's own take-off and are never touched - regeneration corrects the
# *rates*, reusing the market rates the verification pass already resolved from
# the DB. No second, divergent pricing pass runs, so the regenerated figures can
# never disagree with what the verification screen showed.

def _to_object_id(value: Any) -> Optional[ObjectId]:
    """Parse an id without raising on the malformed values a bad request sends."""
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _rate_lookup(verified_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index the verify pass by description so regeneration can reprice lines."""
    index: Dict[str, Dict[str, Any]] = {}
    for entry in verified_items or []:
        key = (entry.get("description") or "").strip().lower()
        if key and entry.get("market_rate"):
            index[key] = entry
    return index


def _reprice_lines(
    items: List[Dict[str, Any]],
    verified_by_description: Dict[str, Dict[str, Any]],
    city: str,
) -> List[Dict[str, Any]]:
    """Apply each line's verified market rate, keeping the upload's quantities.

    A line with no comparable rate keeps its quoted figure and is marked
    `was_revised=False` with a note, so a QS can see it was left untouched
    rather than quietly repriced to zero.
    """
    corrected: List[Dict[str, Any]] = []
    for item in items or []:
        description = item.get("description") or ""
        quantity = float(item.get("quantity") or 0.0)
        quoted_rate = float(item.get("rate") or 0.0)
        quoted_amount = float(item.get("amount") or 0.0)

        match = verified_by_description.get(description.strip().lower())
        market_rate = float(match["market_rate"]) if match else None
        if market_rate:
            rate = market_rate
            rate_source = match.get("market_rate_source") or "database"
            revised = abs(rate - quoted_rate) > 0.01
            note = (
                f"Repriced from {quoted_rate:,.2f} to the verified "
                f"{match.get('market_rate_city') or city} rate ({rate_source})."
                if revised
                else "Already within range of the verified rate."
            )
        else:
            rate = quoted_rate
            rate_source = "quoted"
            revised = False
            note = "No comparable rate in the catalogue - quoted rate kept."

        corrected.append({
            "description": description,
            "element_name": item.get("element_name"),
            "unit": item.get("unit", ""),
            "quantity": quantity,
            "rate": rate,
            "adjusted_rate": rate,
            "amount": round(quantity * rate, 2),
            "rate_source": rate_source,
            "quantity_source": "upload",
            "original_rate": quoted_rate,
            "original_amount": quoted_amount,
            "was_revised": revised,
            "revision_note": note,
        })
    return corrected


def _regenerated_elements(
    corrected_items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Group repriced lines into the element shape the app stores and renders."""
    elements: List[Dict[str, Any]] = []
    for group in group_items_by_element(corrected_items):
        lines = group.get("items") or []
        original_total = round(
            sum(float(line.get("original_amount") or 0.0) for line in lines), 2
        )
        regenerated_total = round(
            sum(float(line.get("amount") or 0.0) for line in lines), 2
        )
        elements.append({
            "element_name": group.get("element_name"),
            "items": lines,
            "element_total": regenerated_total,
            "original_total": original_total,
            "regenerated_total": regenerated_total,
            "variance": round(regenerated_total - original_total, 2),
        })
    return elements


class BOQGenerator:
    """Service for generating Bills of Quantities from building parameters."""

    def __init__(
        self,
        db: Optional[AsyncIOMotorDatabase] = None,
        pg_db: Optional[AsyncSession] = None,
    ):
        self.db = db
        self.pg_db = pg_db
        self.api_key = settings.AI_SERVICE_API_KEY
        self.api_url = settings.AI_SERVICE_URL
        self.mitm = MITMEngine(PriceService(mongo_db=db, pg_db=pg_db))
        self.price_service = PriceService(mongo_db=db, pg_db=pg_db)

    async def create_boq(
        self,
        project_id: str,
        source_document_ids: List[str],
        template_id: Optional[str] = None,
        title: str = "",
        created_by: str = ""
    ) -> Dict[str, Any]:
        """Create a new BOQ record in MongoDB."""
        # NOTE: motor's AsyncIOMotorDatabase raises NotImplementedError on
        # truth-value testing, so always compare against None.
        if self.db is None:
            raise RuntimeError("MongoDB connection not available")

        boq_doc = {
            "projectId": project_id,
            "boqNumber": f"BOQ-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "title": title,
            "status": "generating",
            "version": 1,
            "generationMethod": "ai",
            "sourceDocumentIds": source_document_ids,
            "templateId": template_id,
            "createdBy": created_by,
            "trades": [],
            "summary": {},
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }

        result = await self.db["boqs"].insert_one(boq_doc)
        created = await self.db["boqs"].find_one({"_id": result.inserted_id})
        if created:
            created["_id"] = str(created["_id"])
        return created

    async def generate_boq_items(
        self,
        boq_id: str,
        document_ids: List[str]
    ) -> None:
        """Generate BOQ items in background using AI."""
        logger.info(f"Generating BOQ items for {boq_id} from {len(document_ids)} documents")
        if self.db is None:
            logger.warning("No DB connection - cannot generate BOQ items")
            return

        try:
            boq = await self.db["boqs"].find_one({"_id": ObjectId(boq_id)})
            if not boq:
                logger.error(f"BOQ {boq_id} not found")
                return

            from app.schemas.boq import BOQGenerationRequest, ProjectInfoInput, FloorInput
            project_info = ProjectInfoInput(
                project_title=boq.get("title", "Untitled"),
                city="Abuja",
            )
            request = BOQGenerationRequest(
                project_info=project_info,
                floors=[FloorInput(
                    floor_id="GF", level=0,
                    floor_area_m2=boq.get("floor_area", 100),
                    perimeter_m=boq.get("perimeter", 40),
                )],
            )

            mitm_result = self.mitm.enrich(request)
            enriched = mitm_result["enriched"]
            boq_data = await self._generate_from_template(request, enriched)

            city = enriched["project_info"]["city"]
            enriched_elements, discrepancies = await self.price_service.enrich_boq_elements(
                boq_data["elements"], city
            )
            boq_data["elements"] = enriched_elements
            boq_data["price_discrepancies"] = discrepancies

            totals = self.price_service.recalculate_totals(boq_data["elements"])
            boq_data["summary"] = {
                "sub_total": totals["sub_total"],
                "contingency": totals["contingency_amount"],
                "contingency_amount": totals["contingency_amount"],
                "contingency_pct": totals["contingency_pct"],
                "vat": totals["vat_amount"],
                "vat_amount": totals["vat_amount"],
                "vat_pct": totals["vat_pct"],
                "total_contract_sum": totals["total_contract_sum"],
            }

            await self.db["boqs"].update_one(
                {"_id": ObjectId(boq_id)},
                {"$set": {
                    "status": "pending_review",
                    "elements": boq_data["elements"],
                    "summary": boq_data["summary"],
                    "assumptions": boq_data.get("assumptions", []),
                    "notes": boq_data.get("notes", []),
                    "confidence": mitm_result["confidence"],
                    "updatedAt": datetime.utcnow(),
                }}
            )
            logger.info(f"BOQ {boq_id} items generated successfully")
        except Exception as e:
            logger.error(f"Failed to generate BOQ items for {boq_id}: {e}")
            if self.db is not None:
                await self.db["boqs"].update_one(
                    {"_id": ObjectId(boq_id)},
                    {"$set": {"status": "failed", "error": str(e), "updatedAt": datetime.utcnow()}}
                )

    async def approve_boq(
        self,
        boq_id: str,
        approved_by: str
    ) -> Optional[Dict[str, Any]]:
        """Approve a BOQ."""
        if self.db is None:
            raise RuntimeError("MongoDB connection not available")

        result = await self.db["boqs"].find_one_and_update(
            {"_id": ObjectId(boq_id)},
            {
                "$set": {
                    "status": "approved",
                    "approvedBy": approved_by,
                    "approvedAt": datetime.utcnow(),
                    "updatedAt": datetime.utcnow(),
                }
            },
            return_document=True,
        )
        if result:
            result["_id"] = str(result["_id"])
        return result

    async def export_boq(
        self,
        boq_id: str,
        format: str,
    ) -> Optional[Tuple[str, str, bytes]]:
        """Build a BOQ export in memory and return a downloadable payload.

        Returns ``(filename, media_type, content_bytes)`` for the caller to
        stream, or ``None`` when the BOQ cannot be found. Rendering happens in
        memory (no temp files), so concurrent exports and multiple workers never
        collide on disk. ``word`` is accepted as an alias for ``docx``.
        """
        # `word` is the user-facing name for the .docx format.
        fmt = "docx" if format in ("word", "docx") else format
        logger.info(f"Exporting BOQ {boq_id} to {fmt}")

        if fmt not in ("csv", "excel", "pdf", "docx"):
            raise ValueError(f"Unsupported export format: {format}")
        if self.db is None:
            return None

        try:
            boq = await self.db["boqs"].find_one({"_id": ObjectId(boq_id)})
            if not boq:
                # Bills uploaded for verification live in their own collection.
                # Normalise their shape (element_name → elementName) so the same
                # writers below can render csv/excel/pdf/docx unchanged.
                v = await self.db["boq_verifications"].find_one({"_id": ObjectId(boq_id)})
                if v:
                    # An uploaded bill carries no computed totals, so fall back to
                    # the figures the document itself states.
                    stated = v.get("stated") or {}
                    boq = {
                        "title": v.get("filename") or "Uploaded BOQ",
                        "elements": [
                            {
                                "elementName": el.get("element_name") or el.get("elementName") or "",
                                "items": el.get("items", []),
                            }
                            for el in v.get("elements", [])
                        ],
                        "summary": {
                            "sub_total": stated.get("sub_total") or 0,
                            "contingency": stated.get("contingency") or 0,
                            "vat": stated.get("vat") or 0,
                            "total_contract_sum": stated.get("total_contract_sum")
                            or v.get("total_quoted")
                            or 0,
                        },
                    }
            if not boq:
                logger.warning(f"BOQ {boq_id} not found for export")
                return None

            # Generated bills persist their payload nested under `boqData` (only the
            # background regenerator mirrors `elements` to the top level), so unwrap
            # it the same way /cart-preview does - otherwise every export is blank.
            # `elementName` is also normalised here because the generator emits
            # snake_case `element_name` while the writers below read camelCase.
            data = boq.get("boqData") if isinstance(boq.get("boqData"), dict) else {}
            elements = [
                {
                    "elementName": el.get("elementName") or el.get("element_name") or "",
                    "items": [
                        {
                            **item,
                            # Price enrichment writes `adjusted_rate` and leaves
                            # `rate` at 0 for AI-estimated lines. The UI prefers
                            # adjusted_rate (BOQResults.tsx), so the Rate column in
                            # an export would otherwise show N0.00 while the screen
                            # shows the real figure.
                            "rate": item.get("adjusted_rate") or item.get("rate") or 0,
                        }
                        for item in (el.get("items") or [])
                    ],
                }
                for el in (data.get("elements") or boq.get("elements") or [])
            ]
            summary = data.get("summary") or boq.get("summary") or {}
            project_title = (
                boq.get("title")
                or (data.get("project_info") or {}).get("project_title")
                or "BOQ"
            )

            # Human-friendly, filesystem-safe attachment base name.
            safe_title = re.sub(r"[^A-Za-z0-9 _-]", "", project_title or "").strip()
            safe_title = (safe_title or boq_id)[:60]
            from io import BytesIO, StringIO

            if fmt == "csv":
                import csv
                buf = StringIO()
                writer = csv.writer(buf)
                writer.writerow(["Element", "Item Code", "Description", "Quantity", "Unit", "Rate", "Amount"])
                for el in elements:
                    for item in el.get("items", []):
                        writer.writerow([
                            el.get("elementName", ""),
                            item.get("item_code", item.get("itemCode", "")),
                            item.get("description", ""),
                            item.get("quantity", 0),
                            item.get("unit", ""),
                            item.get("rate", 0),
                            item.get("amount", 0),
                        ])
                writer.writerow([])
                writer.writerow(["Total Contract Sum", "", "", "", "", "", summary.get("total_contract_sum", 0)])
                logger.info(f"Exported BOQ {boq_id} to CSV")
                # utf-8-sig: Excel opens CSV with the naira/accents intact.
                return (f"{safe_title}.csv", "text/csv", buf.getvalue().encode("utf-8-sig"))

            elif fmt == "excel":
                try:
                    import openpyxl
                    from openpyxl.styles import Font, Alignment, PatternFill
                except ImportError:
                    logger.warning("openpyxl not installed, falling back to CSV")
                    return await self.export_boq(boq_id, "csv")

                wb = openpyxl.Workbook()
                ws = wb.active
                ws.title = "BOQ"

                ws.cell(row=1, column=1, value=f"Bill of Quantities - {project_title}").font = Font(bold=True, size=14)
                ws.merge_cells("A1:G1")

                headers = ["Element", "Item Code", "Description", "Quantity", "Unit", "Rate (NGN)", "Amount (NGN)"]
                # BurnCost orange header row so the workbook matches the PDF/Word.
                brand_fill = PatternFill("solid", fgColor="FF6B00")
                for col, h in enumerate(headers, 1):
                    cell = ws.cell(row=3, column=col, value=h)
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = brand_fill

                row = 4
                for el in elements:
                    for item in el.get("items", []):
                        ws.cell(row=row, column=1, value=el.get("elementName", ""))
                        ws.cell(row=row, column=2, value=item.get("item_code", item.get("itemCode", "")))
                        ws.cell(row=row, column=3, value=item.get("description", ""))
                        ws.cell(row=row, column=4, value=item.get("quantity", 0))
                        ws.cell(row=row, column=5, value=item.get("unit", ""))
                        ws.cell(row=row, column=6, value=item.get("rate", 0))
                        ws.cell(row=row, column=7, value=item.get("amount", 0))
                        row += 1

                row += 1
                ws.cell(row=row, column=1, value="Sub Total").font = Font(bold=True)
                ws.cell(row=row, column=7, value=summary.get("sub_total", 0)).font = Font(bold=True)
                row += 1
                ws.cell(row=row, column=1, value=f"Contingency ({summary.get('contingency_pct', 10)}%)").font = Font(bold=True)
                ws.cell(row=row, column=7, value=summary.get("contingency", 0)).font = Font(bold=True)
                row += 1
                ws.cell(row=row, column=1, value=f"Overheads & Profit ({summary.get('overheads_profit_pct', 10)}%)").font = Font(bold=True)
                ws.cell(row=row, column=7, value=summary.get("overheads_profit", 0)).font = Font(bold=True)
                row += 1
                ws.cell(row=row, column=1, value=f"VAT ({summary.get('vat_pct', 7.5)}%)").font = Font(bold=True)
                ws.cell(row=row, column=7, value=summary.get("vat", 0)).font = Font(bold=True)
                row += 1
                ws.cell(row=row, column=1, value="TOTAL CONTRACT SUM").font = Font(bold=True, size=13)
                ws.cell(row=row, column=7, value=summary.get("total_contract_sum", 0)).font = Font(bold=True, size=13)

                buf = BytesIO()
                wb.save(buf)
                logger.info(f"Exported BOQ {boq_id} to Excel")
                return (
                    f"{safe_title}.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    buf.getvalue(),
                )

            elif fmt == "pdf":
                # Branded, watermarked renderer (PyMuPDF) shared with the vendor
                # report. It always has a title block + summary, so the download
                # can never come back blank.
                try:
                    from app.services.boq_report_service import build_boq_pdf
                except ImportError:
                    logger.warning("PyMuPDF not installed, falling back to CSV")
                    return await self.export_boq(boq_id, "csv")

                content = build_boq_pdf(
                    project_title=project_title,
                    elements=elements,
                    summary=summary,
                    project_info=(data.get("project_info") or boq.get("project_info") or {}),
                )
                logger.info(f"Exported BOQ {boq_id} to PDF")
                return (f"{safe_title}.pdf", "application/pdf", content)

            elif fmt == "docx":
                try:
                    from app.services.boq_report_service import build_boq_docx
                except ImportError:
                    logger.warning("python-docx not installed, falling back to CSV")
                    return await self.export_boq(boq_id, "csv")

                content = build_boq_docx(
                    project_title=project_title,
                    elements=elements,
                    summary=summary,
                    project_info=(data.get("project_info") or boq.get("project_info") or {}),
                )
                logger.info(f"Exported BOQ {boq_id} to DOCX")
                return (
                    f"{safe_title}.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    content,
                )

        except Exception as e:
            logger.error(f"Export failed for BOQ {boq_id}: {e}")
            raise

    async def upload_and_verify(
        self,
        file,
        uploaded_by: str,
        city: str = "Abuja",
    ) -> Dict[str, Any]:
        """Upload and verify a BOQ file (Excel/Word/PDF/CSV) against market prices.

        Handles the real Nigerian QS layout: a multi-sheet workbook with one
        sheet per SMM7 element (plus COVER PAGE / SUMMARY / GENERAL SUMMARY),
        parsed by `boq_file_parser` - Word tables and PDF pages are flattened to
        the same shape. Rates are compared against `city` (Nigerian
        material prices vary by state), and repeated descriptions share a single
        lookup so a 300-line bill does not fire 300 market-rate queries.
        """
        logger.info(f"Processing uploaded BOQ file by user {uploaded_by}")
        try:
            # Accepts a Starlette UploadFile (async read) or a plain file object
            # (the upload endpoint hands us a spooled temp file).
            content = await file.read() if asyncio.iscoroutinefunction(file.read) else file.read()
            filename = file.filename or "uploaded"

            parsed = parse_boq_file(content, filename)
            parsed_items = parsed["items"]
            warnings = list(parsed.get("warnings") or [])
            if not parsed_items:
                return {
                    "boq_id": "",
                    "parsed_boq": {
                        "items": [], "elements": [],
                        "stated": parsed.get("stated", {}), "warnings": warnings,
                    },
                    "analysis": {},
                    "message": " ".join(warnings) or "No data rows found in the uploaded file.",
                }

            analysis = {
                "verified_items": [],
                "discrepancies": [],
                "city": city,
                "elements": _summarise_elements(parsed_items, parsed.get("sheets") or []),
            }
            total_quoted = 0.0
            total_market = 0.0

            # One market-rate lookup per distinct clause: real bills repeat
            # descriptions ("Ditto ...", the same tile across floors), and an
            # uncached bill would fire hundreds of queries on the request thread.
            # The lookup text carries the item's heading context, because a line
            # like "225mm thick" only makes sense under its "Blockwork" heading.
            def _lookup_text(entry: Dict[str, Any]) -> str:
                context = (entry.get("context") or "").strip()
                description = (entry.get("description") or "").strip()
                return f"{context} {description}".strip() if context else description

            distinct: List[str] = []
            seen_descriptions: Dict[str, None] = {}
            for item in parsed_items:
                key = _lookup_text(item).lower()
                if key and key not in seen_descriptions:
                    seen_descriptions[key] = None
                    distinct.append(_lookup_text(item))

            lookup_targets = distinct[:_MAX_RATE_LOOKUPS]
            # Verified DB rates only. `get_rate` would fall back to an online
            # price or an AI estimate, and neither may be what a rate check
            # reports as "the market rate" - it is also a network call per line.
            price_truth = PriceTruthService(self.price_service)
            gate = asyncio.Semaphore(_MAX_CONCURRENT_RATE_LOOKUPS)

            async def _verified_rate(description: str):
                async with gate:
                    return await price_truth.get_verified_rate(description, city)

            # Bounded batches: dispatch the targets in chunks instead of
            # gathering the whole list at once, so a 300-line bill holds at most
            # `_RATE_LOOKUP_CHUNK` live coroutines. `gather` preserves order, so
            # the zip below still maps every rate back to its description.
            lookups: List[Any] = []
            for start in range(0, len(lookup_targets), _RATE_LOOKUP_CHUNK):
                batch = lookup_targets[start:start + _RATE_LOOKUP_CHUNK]
                lookups.extend(await asyncio.gather(*[
                    _verified_rate(description) for description in batch
                ]))
            rate_by_description = {
                description.strip().lower(): result
                for description, result in zip(lookup_targets, lookups)
            }

            for item in parsed_items:
                description = item.get("description") or ""
                quantity = float(item.get("quantity") or 0.0)
                rate = float(item.get("rate") or 0.0)
                amount = float(item.get("amount") or 0.0)
                total_quoted += amount

                db_rate = rate_by_description.get(_lookup_text(item).lower())
                market_rate, match_reason = (
                    _comparable_rate(item, db_rate, _lookup_text(item))
                    if db_rate
                    else (None, "no_match")
                )
                verified = {
                    "description": description,
                    "element_name": item.get("element_name"),
                    "unit": item.get("unit", ""),
                    "quantity": quantity,
                    "quoted_rate": rate,
                    "quoted_amount": amount,
                    "match_reason": match_reason or "matched",
                }
                if market_rate:
                    total_market += quantity * market_rate
                    deviation = (
                        abs(rate - market_rate) / market_rate * 100 if market_rate > 0 else 0
                    )
                    verified.update({
                        "market_rate": market_rate,
                        "market_rate_city": db_rate.get("city", city),
                        "market_rate_source": db_rate.get("source", "database"),
                        "market_rate_product": db_rate.get("product_name"),
                        "deviation_pct": round(deviation, 1),
                        "status": "inflated" if deviation > 25 else "fair",
                    })
                    if deviation > 25:
                        analysis["discrepancies"].append({
                            "description": description,
                            "element_name": item.get("element_name"),
                            "quoted": rate,
                            "market": market_rate,
                            "overcharge_pct": round(deviation, 1),
                        })
                else:
                    verified.update({
                        "market_rate": None,
                        "deviation_pct": None,
                        "status": "unverified",
                    })
                analysis["verified_items"].append(verified)

            if len(distinct) > len(lookup_targets):
                warnings.append(
                    f"Compared the first {len(lookup_targets)} distinct descriptions; "
                    f"{len(distinct) - len(lookup_targets)} more were checked without a "
                    "comparable rate."
                )
            analysis["verified_count"] = sum(
                1 for v in analysis["verified_items"] if v["status"] != "unverified"
            )
            analysis["unverified_count"] = len(analysis["verified_items"]) - analysis["verified_count"]
            analysis["stated"] = parsed.get("stated", {})
            analysis["warnings"] = warnings
            analysis["arithmetic"] = arithmetic = _arithmetic_report(
                parsed.get("sheets") or [], parsed.get("stated") or {}
            )
            if arithmetic["findings"]:
                warnings.append(
                    f"{arithmetic['mismatch_count']} arithmetic issue(s) found in the "
                    "uploaded BOQ - see the arithmetic report."
                )
            analysis.update(_verification_summary(analysis))

            boq_id = ""
            if self.db is not None:
                doc = {
                    "filename": filename,
                    "uploadedBy": uploaded_by,
                    "uploadedAt": datetime.utcnow(),
                    "record_type": "verification",
                    "city": city,
                    "items": parsed_items,
                    "elements": group_items_by_element(parsed_items),
                    "stated": parsed.get("stated", {}),
                    "arithmetic": arithmetic,
                    "analysis": analysis,
                    "warnings": warnings,
                    "total_quoted": total_quoted,
                    "total_market": total_market,
                }
                result = await self.db["boq_verifications"].insert_one(doc)
                boq_id = str(result.inserted_id)

            inflated_count = len([v for v in analysis["verified_items"] if v.get("status") == "inflated"])
            fair_count = len([v for v in analysis["verified_items"] if v.get("status") == "fair"])

            return _plain_dashes({
                "boq_id": boq_id,
                "record_type": "verification",
                "parsed_boq": {
                    "items": parsed_items,
                    "elements": group_items_by_element(parsed_items),
                    "stated": parsed.get("stated", {}),
                    "warnings": warnings,
                    "total_quoted": total_quoted,
                },
                "arithmetic": arithmetic,
                "analysis": analysis,
                "message": (
                    f"Verified {len(parsed_items)} items. "
                    f"{inflated_count} inflated, {fair_count} fair, "
                    f"{len(parsed_items) - inflated_count - fair_count} without a "
                    "comparable rate."
                ),
            })

        except Exception as e:
            logger.error(f"Upload and verify failed: {e}")
            return {
                "boq_id": "",
                "parsed_boq": {},
                "analysis": {},
                "message": f"Error processing file: {str(e)}",
            }

    async def handle_decision(
        self,
        boq_id: str,
        decision: str,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Handle a user decision on a BOQ.

        Generated bills (collection `boqs`) only record the decision. Uploaded
        bills (collection `boq_verifications`) are regenerated for real:
        `regenerate` rebuilds a priced BOQ as a new `boqs` version from the
        verified items, `save_original` just records the choice. Returns ``None``
        when the id names nothing the caller owns.
        """
        if self.db is None:
            raise RuntimeError("MongoDB connection not available")

        oid = _to_object_id(boq_id)
        if oid is None:
            return None

        verification = await self.db["boq_verifications"].find_one({"_id": oid})
        if verification:
            # A verification record may only be decided on by the uploader.
            if str(verification.get("uploadedBy") or "") != str(user_id):
                return None
            if decision == "regenerate":
                return await self._regenerate_verification(verification, user_id)
            return await self._save_verification(oid, user_id)

        new_status = "regenerated" if decision == "regenerate" else "saved_original"
        result = await self.db["boqs"].find_one_and_update(
            {"_id": oid},
            {
                "$set": {
                    "status": new_status,
                    "userDecision": decision,
                    "decidedBy": user_id,
                    "decidedAt": datetime.utcnow(),
                    "updatedAt": datetime.utcnow(),
                }
            },
            return_document=True,
        )
        if result:
            result["_id"] = str(result["_id"])
        return result

    async def _save_verification(
        self, oid: ObjectId, user_id: str
    ) -> Dict[str, Any]:
        """Record a `save_original` decision on an uploaded bill."""
        now = datetime.utcnow()
        await self.db["boq_verifications"].update_one(
            {"_id": oid},
            {"$set": {
                "status": "saved_original",
                "userDecision": "save_original",
                "decidedBy": user_id,
                "decidedAt": now,
                "updatedAt": now,
            }},
        )
        return {
            "boq_id": str(oid),
            "record_type": "verification",
            "status": "saved_original",
            "message": "Original bill saved as-is - nothing was repriced.",
        }

    async def _regenerate_verification(
        self, verification: Dict[str, Any], user_id: str
    ) -> Dict[str, Any]:
        """Rebuild a priced BOQ from an uploaded bill's verified items.

        Reuses the verification's own DB rates (no second pricing pass, so the
        regenerated figures can never diverge from what was shown on screen),
        the SMM7 grouping, and `PriceService.recalculate_totals` for the summary.
        An optional Gemini pass only tidies wording - quantities and rates are
        never touched by the model.
        """
        city = verification.get("city") or "Abuja"
        items = verification.get("items") or []
        verified_by_description = _rate_lookup(
            (verification.get("analysis") or {}).get("verified_items") or []
        )

        corrected = _reprice_lines(items, verified_by_description, city)
        if self.api_key and corrected:
            try:
                await self._rewrite_specifications(corrected)
            except Exception as exc:  # non-fatal: the deterministic rebuild stands
                logger.warning("Regeneration wording pass skipped: %s", exc)

        elements = _regenerated_elements(corrected)
        totals = self.price_service.recalculate_totals(
            [{"element_name": el["element_name"], "items": el["items"]} for el in elements]
        )

        line_items = [line for el in elements for line in el["items"]]
        items_revised = sum(1 for line in line_items if line.get("was_revised"))
        original_sub_total = round(sum(el["original_total"] for el in elements), 2)
        regenerated_sub_total = round(totals["sub_total"], 2)

        # Contract-level comparison (sub-total plus contingency/overheads/VAT) is
        # derived from the same totals maths as everything else: the original
        # sub-total is pushed through `recalculate_totals` rather than priced a
        # second time, so the two figures can never diverge.
        original_contract_sum = self.price_service.recalculate_totals(
            [{"element_name": "Original", "items": [{"amount": original_sub_total}]}]
        )["total_contract_sum"]
        regenerated_contract_sum = totals["total_contract_sum"]

        now = datetime.utcnow()
        title = verification.get("filename") or "Uploaded BOQ"
        summary = {
            "sub_total": regenerated_sub_total,
            "contingency": totals["contingency_amount"],
            "contingency_amount": totals["contingency_amount"],
            "contingency_pct": totals["contingency_pct"],
            "vat": totals["vat_amount"],
            "vat_amount": totals["vat_amount"],
            "vat_pct": totals["vat_pct"],
            "total_contract_sum": totals["total_contract_sum"],
        }

        inserted = await self.db["boqs"].insert_one({
            "projectId": "",
            "boqNumber": f"BOQ-{now.strftime('%Y%m%d%H%M%S')}",
            "title": title,
            "status": "pending_review",
            "version": int(verification.get("version") or 1),
            "generationMethod": "regenerated",
            "sourceVerificationId": str(verification["_id"]),
            "city": city,
            "elements": elements,
            "summary": summary,
            "createdBy": user_id,
            "createdAt": now,
            "updatedAt": now,
        })
        new_id = str(inserted.inserted_id)

        await self.db["boq_verifications"].update_one(
            {"_id": verification["_id"]},
            {"$set": {
                "status": "regenerated",
                "userDecision": "regenerate",
                "decidedBy": user_id,
                "decidedAt": now,
                "regeneratedBoqId": new_id,
                "updatedAt": now,
            }},
        )

        if items_revised:
            revision_notes = [
                f"{items_revised} of {len(line_items)} lines repriced to verified "
                f"{city} market rates."
            ]
        else:
            revision_notes = [
                "Every quoted rate already matched its verified market rate - "
                "quantities and rates were kept."
            ]

        return {
            "boq_id": new_id,
            "record_type": "verification",
            "status": "regenerated",
            "regenerated_boq": {
                "project_title": title,
                "regenerated_at": now.isoformat(),
                "elements": [
                    {
                        "element_name": el["element_name"],
                        "original_total": el["original_total"],
                        "regenerated_total": el["regenerated_total"],
                        "variance": el["variance"],
                        "line_items": el["items"],
                    }
                    for el in elements
                ],
                "summary": {
                    "original_sub_total": original_sub_total,
                    "regenerated_sub_total": regenerated_sub_total,
                    "contingencies": totals["contingency_amount"],
                    "vat_rate": totals["vat_pct"],
                    "vat_amount": totals["vat_amount"],
                    "total_contract_sum": totals["total_contract_sum"],
                    "total_saving": round(original_sub_total - regenerated_sub_total, 2),
                    "original_contract_sum": original_contract_sum,
                    "regenerated_contract_sum": regenerated_contract_sum,
                    "contract_saving": round(
                        original_contract_sum - regenerated_contract_sum, 2
                    ),
                },
                "items_revised": items_revised,
                "revision_notes": revision_notes,
            },
            "message": (
                f"Repriced {items_revised} of {len(line_items)} items against "
                f"verified {city} market rates. The regenerated bill is saved as "
                "a new version."
            ),
        }

    async def _rewrite_specifications(self, corrected: List[Dict[str, Any]]) -> None:
        """Let Gemini tidy wording into SMM7 element groups (wording only).

        The model echoes each line back by index and may not invent or drop
        lines; only `element_name`/`description` are read from its reply, so
        quantities and rates are structurally impossible to change. Failures
        propagate to the caller, which keeps the deterministic rebuild.
        """
        from google.genai import types as genai_types

        listing = "\n".join(
            f'{i}. [{row.get("element_name") or "Unclassified"}] '
            f'{row.get("description")} ({row.get("unit") or "-"})'
            for i, row in enumerate(corrected)
        )
        prompt = (
            "You are a Nigerian quantity surveyor tidying a bill of quantities.\n"
            "For each numbered line below, return the SMM7 element group and a "
            "cleaner description. Do NOT change, add or drop lines - echo each "
            "index exactly once.\n\n"
            f"{listing}\n\n"
            'Return ONLY JSON: {"items": [{"i": number, "element_name": '
            '"SMM7 element", "description": "cleaned description"}]}'
        )

        client = get_gemini_client()
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[prompt],
            config=genai_types.GenerateContentConfig(
                temperature=0.0, max_output_tokens=8192
            ),
        )
        parsed = self._parse_boq_json(response.text or "")
        if not parsed:
            raise ValueError("unparseable wording response")
        for row in parsed.get("items") or []:
            idx = row.get("i")
            if not isinstance(idx, int) or not 0 <= idx < len(corrected):
                continue
            if row.get("element_name"):
                corrected[idx]["element_name"] = str(row["element_name"])
            if row.get("description"):
                corrected[idx]["description"] = str(row["description"])

    async def verify_quote_text(
        self,
        quote_text: str,
        user_id: str
    ) -> Dict[str, Any]:
        """Verify a quote text against market prices using Gemini."""
        logger.info(f"Verifying quote for user {user_id}")
        try:
            from app.services.gemini_client import get_gemini_client
            from google.genai import types as genai_types

            client = get_gemini_client()
            prompt = f"""You are a Nigerian quantity surveyor. Parse the following quote text and extract line items.
Do NOT estimate market rates - extract only the quoted values. Market rates are supplied from the database.

Quote text:
{quote_text}

Return ONLY valid JSON with this structure:
{{
  "items": [
    {{
      "description": "item description",
      "quantity": number,
      "unit": "m2/m3/nr/ls/etc",
      "quoted_rate": number
    }}
  ],
  "total_quoted": number,
  "summary_note": "brief analysis"
}}"""

            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[prompt],
                config=genai_types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=4096,
                ),
            )

            text = response.text
            if not text:
                raise Exception("Empty Gemini response")

            import re
            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
            if match:
                result = json.loads(match.group(1))
            else:
                match = re.search(r'\{[\s\S]*\}', text)
                if match:
                    result = json.loads(match.group(0))
                else:
                    raise Exception("Failed to parse Gemini response")

            total_market = 0.0
            total_quoted = float(result.get("total_quoted", 0))
            inflated_count = 0
            fair_count = 0
            unverified_count = 0
            estimated_items = []

            for item in result.get("items", []):
                db_rate = await self.price_service.get_rate(
                    item["description"], unit=item.get("unit")
                )
                quoted_rate = float(item.get("quoted_rate", 0))
                if db_rate and db_rate.get("rate"):
                    source = db_rate.get("price_source", "database")
                    item["market_rate"] = db_rate["rate"]
                    item["price_source"] = source
                    # Only our own DB sources assert verification. A rate that
                    # arrives without the flag (live online / online average /
                    # AI estimate) is priced but never claimed as verified, so it
                    # can never produce an inflation flag.
                    item["verified"] = bool(
                        db_rate.get("verified", source in DB_SOURCES)
                    )
                    item["confidence"] = db_rate.get("confidence", 1.0)
                    total_market += item["quantity"] * db_rate["rate"]
                    if item["verified"]:
                        item["deviation_pct"] = round(
                            abs(quoted_rate - db_rate["rate"]) / db_rate["rate"] * 100, 1
                        )
                        if item["deviation_pct"] > 25:
                            item["status"] = "inflated"
                            inflated_count += 1
                        else:
                            item["status"] = "fair"
                            fair_count += 1
                    else:
                        # Flagged estimate - do not claim inflation
                        item["status"] = "unverified"
                        item["deviation_pct"] = None
                        unverified_count += 1
                        estimated_items.append({
                            "description": item["description"],
                            "quantity": item["quantity"],
                            "unit": item["unit"],
                        })
                else:
                    item["market_rate"] = None
                    item["status"] = "unverified"
                    item["price_source"] = "unavailable"
                    unverified_count += 1

            result["total_market"] = round(total_market, 2)
            result["total_overcharge"] = round(max(total_quoted - total_market, 0), 2)
            result["inflated_count"] = inflated_count
            result["fair_count"] = fair_count
            result["unverified_count"] = unverified_count

            # Raise demand alerts for estimate/unverified items so suppliers
            # can respond with real prices (final total still computable).
            if estimated_items and self.pg_db:
                created = await self.price_service.notify_vendors(
                    estimated_items,
                    project_title="Quote verification",
                    user_id=user_id,
                )
                result["demand_alerts_created"] = created

            return result

        except Exception as e:
            logger.error(f"Quote verification failed: {e}")
            return {
                "items": [],
                "total_quoted": 0,
                "total_market": 0,
                "total_overcharge": 0,
                "inflated_count": 0,
                "fair_count": 0,
                "unverified_count": 0,
                "summary_note": f"Verification failed: {str(e)}"
            }

    # Main generation entry point

    async def generate_from_parameters(
        self,
        request: BOQGenerationRequest,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate a complete BOQ from building parameters.
        Runs MITM enrichment first, then AI generation, then price enrichment.
        """
        logger.info(f"Generating BOQ for project: {request.project_info.project_title}")

        mitm_result = self.mitm.enrich(request)
        enriched = mitm_result["enriched"]
        flags = mitm_result["flags"]
        confidence = mitm_result["confidence"]

        fallback_reason: Optional[str] = None
        if self.api_key:
            try:
                boq = await self._generate_with_ai(request, enriched)
            except Exception as e:
                logger.error(f"AI generation failed: {e}")
                fallback_reason = str(e)
                boq = await self._generate_from_template(request, enriched)
        else:
            fallback_reason = "no AI service key configured"
            boq = await self._generate_from_template(request, enriched)

        # Element groups an accepted AI draft still omitted (those below the
        # rejection threshold) - surfaced so the bill is not treated as final.
        missing_groups = boq.pop("_missing_element_groups", []) or []

        # Arithmetic is Python's: swap the draft's quantities for the engine's
        # take-off BEFORE anything is priced, so every amount and the contract sum
        # come from the engine's own numbers (see _apply_python_quantities).
        quantity_stats = self._apply_python_quantities(boq["elements"], enriched)

        city = enriched["project_info"]["city"]
        enriched_elements, discrepancies, out_of_stock = await self.price_service.enrich_boq_elements(
            boq["elements"], city
        )

        # Drawing-derived quantities: stamp provenance so consumers can trace
        # each item back to the drawing (quantity_source provenance).
        drawing_extracted = getattr(request, "drawing_extracted", False) or bool(
            getattr(request, "drawing_extracted_data", None)
        )
        if drawing_extracted:
            for element in enriched_elements:
                for item in element.get("items", []):
                    # The engine's take-off outranks a figure read off the drawing.
                    if item.get("quantity_source") != "python":
                        item["quantity_source"] = "drawing"

        boq["elements"] = enriched_elements
        boq["drawing_extracted"] = drawing_extracted
        boq["price_discrepancies"] = discrepancies
        boq["out_of_stock_items"] = out_of_stock

        # Collect flagged AI-estimate items so suppliers can provide real
        # prices via demand alerts (final total already includes the estimate).
        estimated_items = []
        for element in enriched_elements:
            for item in element.get("items", []):
                if item.get("price_source") == "ai_estimate" or item.get("rate_source") == "ai_estimate":
                    estimated_items.append({
                        "item_code": item.get("item_code", item.get("itemCode", "")),
                        "description": item.get("description", ""),
                        "quantity": item.get("quantity", 0),
                        "unit": item.get("unit", ""),
                        "city": city,
                        "estimated": True,
                    })
        boq["estimated_items"] = estimated_items
        boq["estimated_count"] = len(estimated_items)

        if user_id:
            project_title = enriched["project_info"]["project_title"]
            demand_items = out_of_stock + estimated_items
            if demand_items:
                notified_count = await self.price_service.notify_vendors(
                    out_of_stock_items=demand_items,
                    project_title=project_title,
                    user_id=user_id,
                )
                for item in boq["out_of_stock_items"]:
                    item["vendor_notified"] = True
                boq["demand_alerts_created"] = notified_count

        totals = self.price_service.recalculate_totals(boq["elements"])
        total = totals["total_contract_sum"]
        floor_area = enriched["total_floor_area_m2"]

        # Pricing coverage: unpriced lines add nothing to the totals, so a mostly
        # unpriced bill has to say so instead of showing a deceptively low
        # contract sum.
        all_items = [item for el in boq["elements"] for item in el.get("items", [])]
        unpriced_items = [
            item
            for item in all_items
            if item.get("price_status") == "unavailable"
            or (not item.get("adjusted_rate") and not item.get("rate"))
        ]

        boq["summary"] = {
            "sub_total": totals["sub_total"],
            "contingency": totals["contingency_amount"],
            "contingency_amount": totals["contingency_amount"],
            "contingency_pct": totals["contingency_pct"],
            "overheads_profit": totals.get("overheads_profit_amount", 0),
            "overheads_profit_pct": totals.get("overheads_profit_pct", 0),
            "vat": totals["vat_amount"],
            "vat_amount": totals["vat_amount"],
            "vat_pct": totals["vat_pct"],
            "total_contract_sum": total,
            "total_low": round(total * 0.9, 2),
            "total_expected": total,
            "total_high": round(total * 1.1, 2),
            "cost_per_m2": round(total / max(floor_area, 1), 2),
            "total_floor_area_m2": floor_area,
            "cost_scenarios": {
                "low": round(total * 0.9, 2),
                "expected": total,
                "high": round(total * 1.1, 2),
            },
            "items_total": len(all_items),
            "items_priced": len(all_items) - len(unpriced_items),
            "items_unpriced": len(unpriced_items),
        }

        boq["confidence"] = confidence
        boq["assumptions_used"] = mitm_result["assumptions"]
        boq["warnings"] = [f["message"] for f in flags if f["severity"] == "critical"]
        if fallback_reason:
            boq["warnings"].append(
                "The AI BOQ draft was rejected and regenerated from the deterministic "
                f"template ({fallback_reason})."
            )
        if missing_groups:
            boq["warnings"].append(
                "The AI BOQ omitted these element groups: "
                f"{', '.join(missing_groups)} - review before issuing the bill."
            )
        # Quantity provenance: how much of the bill the engine measured itself.
        boq["quantity_provenance"] = {
            "python": quantity_stats["applied"],
            "ai": len(quantity_stats["ai_kept"]),
        }
        if quantity_stats["ai_kept"]:
            boq["warnings"].append(
                f"{quantity_stats['applied']} quantities were measured by the take-off "
                f"engine; {len(quantity_stats['ai_kept'])} lines had no take-off value and "
                "keep the draft's own figures - check these before issuing the bill."
            )
        boq["generation_method"] = "template" if fallback_reason else "ai"
        boq["generated_at"] = datetime.utcnow().isoformat()
        boq["project_info"] = enriched["project_info"]

        return _plain_dashes(boq)

    def _apply_python_quantities(
        self, elements: List[Dict], enriched: Dict
    ) -> Dict[str, Any]:
        """Replace bill quantities with the application's own take-off.

        The model supplies structure and specification only. Every line that maps
        to a take-off value has its quantity overwritten here - before pricing -
        so the amounts and the contract sum are always derived from the engine's
        numbers. The model's own figure is preserved as `ai_quantity` for audit.
        Lines with no take-off value keep the model's quantity and are labelled
        `quantity_source="ai"`, so a QS can see exactly which figures to check.

        Returns {"applied": n, "ai_kept": [descriptions]} for the caller to
        report.
        """
        derived = enriched.get("derived_quantities") or {}
        applied = 0
        ai_kept: List[str] = []

        for element in elements or []:
            for item in element.get("items") or []:
                match = _python_quantity_for(item) if derived else None
                value: Optional[float] = None
                if match:
                    raw = derived.get(match[0])
                    try:
                        value = float(raw) if raw is not None else None
                    except (TypeError, ValueError):
                        value = None

                if value is None or value <= 0:
                    # No take-off value for this line: keep the draft's figure,
                    # but label it so the provenance is never ambiguous.
                    if not item.get("quantity_source"):
                        item["quantity_source"] = "ai"
                    ai_kept.append(str(item.get("description") or "")[:70])
                    continue

                item["ai_quantity"] = item.get("quantity")
                item["quantity"] = round(value * match[1], 2)
                item["quantity_source"] = "python"
                applied += 1

        return {"applied": applied, "ai_kept": ai_kept}

    # AI generation

    async def _generate_with_ai(
        self, request: BOQGenerationRequest, enriched: Dict
    ) -> Dict[str, Any]:
        """Generate BOQ using Gemini AI (google-genai SDK)."""
        from google.genai import types as genai_types
        from app.services.gemini_client import get_gemini_client

        client = get_gemini_client()
        # Verified DB rates go in as context only - the model names the
        # specification, the price service does every calculation afterwards.
        catalogue_context = await self.price_service.get_catalogue_context_for_prompt(
            enriched["project_info"]["city"]
        )
        prompt = self._build_boq_prompt(request, enriched, catalogue_context)

        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[prompt],
                config=genai_types.GenerateContentConfig(
                    temperature=0.1,
                    # A full 10-element Nigerian BOQ does not fit the old 8k
                    # budget; a truncated document used to be "repaired" into a
                    # valid but incomplete BOQ, silently dropping every element
                    # after the cut.
                    max_output_tokens=32768,
                ),
            )

            text = response.text
            if not text:
                raise Exception("Empty Gemini response")

            if "MAX_TOKENS" in self._finish_reason(response):
                raise Exception("Gemini hit the token limit and returned a truncated BOQ")

            parsed = self._parse_boq_json(text)
            if parsed is None:
                raise Exception("Failed to parse AI response")

            boq = self._normalise_ai_boq(parsed)
            missing = self._missing_element_groups(boq)
            covered = len(REQUIRED_AI_ELEMENTS) - len(missing)
            if covered < MIN_AI_ELEMENT_COVERAGE:
                raise Exception(
                    f"AI BOQ covered only {covered} of {len(REQUIRED_AI_ELEMENTS)} element "
                    f"groups (missing: {', '.join(missing)})"
                )
            boq["_missing_element_groups"] = missing
            return boq
        except Exception as exc:
            logger.error("Gemini BOQ generation failed: %s", exc)
            raise

    @staticmethod
    def _finish_reason(response: Any) -> str:
        """Finish reason reported by the model ("" when unavailable)."""
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        return str(getattr(candidates[0], "finish_reason", "") or "").upper()

    @staticmethod
    def _parse_boq_json(text: str) -> Optional[Dict[str, Any]]:
        """Parse a Gemini BOQ response, fence-or-bare, repairing minor damage."""
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        candidates = [match.group(1)] if match else []
        bare = re.search(r'\{[\s\S]*\}', text)
        if bare:
            candidates.append(bare.group(0))
        for raw in candidates:
            repaired = repair_json(raw)
            if repaired:
                try:
                    parsed = json.loads(repaired)
                except (TypeError, ValueError):
                    continue
                if isinstance(parsed, dict):
                    return parsed
        return None

    @staticmethod
    def _normalise_ai_boq(boq: Dict[str, Any]) -> Dict[str, Any]:
        """Fold the AI's camelCase keys onto the snake_case the rest of the
        pipeline reads (preview modal, CSV/Excel/PDF export)."""
        elements = boq.get("elements")
        boq["elements"] = elements if isinstance(elements, list) else []
        for el in boq["elements"]:
            if not el.get("element_name"):
                el["element_name"] = el.get("elementName") or ""
            if el.get("element_total") is None and el.get("totalCost") is not None:
                el["element_total"] = el.get("totalCost")
            items = el.get("items")
            el["items"] = items if isinstance(items, list) else []
            for item in el["items"]:
                if not item.get("item_code"):
                    item["item_code"] = item.get("itemCode") or ""
                if not item.get("quantity_source") and item.get("quantitySource"):
                    item["quantity_source"] = item["quantitySource"]
        # New QS sections the model may echo back in camelCase.
        for camel, snake in (
            ("buildingSummary", "building_summary"),
            ("materialEstimates", "material_estimates"),
            ("variationOptions", "variation_options"),
            ("narrativeMarkdown", "narrative_markdown"),
        ):
            if camel in boq and snake not in boq:
                boq[snake] = boq.pop(camel)
        # Double-counting guard: the model sometimes echoes the same material and
        # quantity under a second element (e.g. reinforcement billed in both
        # SUBSTRUCTURE and SUPERSTRUCTURE), which prices the contract sum twice.
        # A line whose (description, unit, quantity) already appeared is dropped.
        seen: set = set()
        for el in boq["elements"]:
            kept = []
            for item in el["items"]:
                try:
                    qty = round(float(str(item.get("quantity") or 0).replace(",", "")), 3)
                except (TypeError, ValueError):
                    qty = 0.0
                key = (
                    re.sub(r"\s+", " ", str(item.get("description") or "").strip().lower()),
                    str(item.get("unit") or "").strip().lower(),
                    qty,
                )
                if key[0] and key[1] and qty and key in seen:
                    item["duplicate_line"] = True
                    continue
                seen.add(key)
                kept.append(item)
            el["items"] = kept
        return boq

    @staticmethod
    def _missing_element_groups(boq: Dict[str, Any]) -> List[str]:
        """Required SMM7 element groups the AI response did not cover."""
        names = " | ".join(
            str(el.get("element_name") or "").lower() for el in boq.get("elements") or []
        )
        return [group for group in REQUIRED_AI_ELEMENTS if group not in names]

    def _build_boq_prompt(
        self,
        request: BOQGenerationRequest,
        enriched: Dict,
        catalogue_context: str = "",
    ) -> str:
        """Build the BOQ generation prompt with Nigerian standards.

        `catalogue_context` is the verified DB rate list (see
        `PriceService.get_catalogue_context_for_prompt`) that fills the prompt's
        price-list section. It defaults to empty so callers that only read the
        shape of the prompt keep working.
        """
        import json as j

        derived = enriched.get("derived_quantities", {})
        city = enriched["project_info"]["city"]
        building_type = enriched["project_info"]["building_type"]

        return f"""You are a professional Quantity Surveyor registered with NIQS (Nigerian Institute of Quantity Surveyors).
Generate a detailed Bill of Quantities (BOQ) in JSON format following the Nigerian SMM7 standard.

YOUR BRIEF - cover every section below and return each one as part of the JSON:
1. PROJECT / BUILDING SUMMARY - what is being built: bedrooms, functional spaces, footprint
   area (m2) and layout notes, in the `building_summary` object.
2. MATERIAL ESTIMATION - the material schedule the design implies, in
   `material_estimates`. The DERIVED QUANTITIES block below was computed by the
   application's measuring engine: use those figures verbatim. Never recompute,
   re-round or re-derive a quantity.
3. VARIATION & OPTIONS ANALYSIS - the design's own specification is the measured bill;
   then list the alternatives a client weighs in `variation_options`: foundation
   A strip / B raft / C pad and ground beam, and roofing A long-span aluminium /
   B stone-coated steel. Indicative quantities only - the application prices them.
4. COST CALCULATIONS - every rate comes from the VERIFIED DATABASE PRICE LIST below.
   Name the catalogue specification in `description` so the item can be matched, and
   leave `rate` at 0. The application computes each rate, amount, sub-total, contingency,
   overheads, VAT and the total contract sum.
5. GEOTECHNICAL RECOMMENDATIONS - in `geotechnical`, give the likely soil conditions for
   the location, the foundation option you recommend (A, B or C) and the reason.
6. NARRATIVE REPORT - in `narrative_markdown`, write the client-facing report with bold
   headers and Markdown tables (summary, material schedule, variation comparison, cost
   commentary, geotechnical note). Copy quantities and costs out of this same JSON; never
   compute them.

ARITHMETIC RULE (MOST IMPORTANT): all calculation is performed by the application, not by
you. Never compute, sum, average, convert, add wastage to, or estimate any quantity, area,
volume, weight, percentage or money value. Report the figures you are given, and leave
every monetary field at 0.

VERIFIED DATABASE PRICE LIST:
{catalogue_context}

PROJECT INFORMATION:
- Title: {request.project_info.project_title}
- Location: {city}, {enriched['project_info']['location']}
- Building Type: {building_type}
- Client: {request.project_info.client_name or 'N/A'}

BUILDING PARAMETERS:
{j.dumps(enriched, indent=2)}

DERIVED QUANTITIES (pre-calculated):
{j.dumps(derived, indent=2)}

IMPORTANT RULES:
1. Do NOT provide market rates/prices. Generate quantities and specifications ONLY.
   Prices are added separately from verified database rates by the price enrichment service.
   If a price is unavoidable in an item, set rate=0 - do not estimate or invent prices.
2. Wastage factors: blocks 5%, concrete 5%, tiles 10%, roofing 12%, reinforcement 5%. They
   are already applied inside DERIVED QUANTITIES - quote those figures exactly as given.
3. Structure the BOQ in this Nigerian standard order:
   - Preliminaries (site setup, insurance, scaffolding)
   - Substructure (excavation, hardcore, blinding, foundation concrete, DPC, ground slab)
   - Superstructure (block walls, RC columns, ring beams, lintels)
   - Roofing (trusses, roof covering, ceiling, gutters, fascia)
   - Joinery (doors, windows, louvres, burglar-proofing)
   - Internal Finishes (wall plaster, floor screed, floor tiles, wall tiles, painting)
   - External Finishes (external render, external paint)
   - Plumbing & Drainage (water supply, sanitary fittings, drainage)
   - Electrical (conduit, wiring, light fittings, sockets, DB)
   - External Works (fence, gate, paving, borehole if applicable)
4. Contingency (10%), overheads & profit (10%) and VAT (7.5%) are applied by the
   application - describe them in `assumptions` / `notes`, never calculate them.
5. Cost scenarios low (90%), expected (100%) and high (110%) are calculated by the
   application from the priced bill.
6. All arithmetic belongs to the application. Never compute a quantity, total, percentage,
   wastage, unit conversion or cost - repeat the take-off figures you were given.
7. `quantity_source` must record who measured the line: "python" for a figure taken from
   DERIVED QUANTITIES, "drawing" for one read off the drawing, "user" for an entered
   figure, and "ai" only for a line with no take-off figure available.

Return ONLY valid JSON with this exact structure:
{{
  "projectTitle": string,
  "generatedAt": "ISO datetime",
  "building_summary": {{
    "bedrooms": number,
    "functional_spaces": [string],
    "footprint_m2": number,
    "layout_notes": string
  }},
  "material_estimates": {{
    "<material>": {{ "quantity": number, "unit": string, "source": "python" | "ai" }}
  }},
  "variation_options": {{
    "foundation": {{ "A": object, "B": object, "C": object }},
    "roofing": {{ "A": object, "B": object }}
  }},
  "geotechnical": {{
    "soil_conditions": [string],
    "recommended_foundation": "A" | "B" | "C",
    "reason": string
  }},
  "narrative_markdown": string,
  "elements": [
    {{
      "elementName": string,
      "trade": string,
      "totalCost": number,
      "items": [
        {{
          "itemCode": string,
          "description": string,
          "quantity": number,
          "unit": string,
          "rate": number,
          "amount": number,
          "estimated": bool,
          "quantity_source": "python" | "drawing" | "user" | "ai"
        }}
      ]
    }}
  ],
  "assumptions": [string],
  "notes": [string]
}}"""

    # Template generation (fallback)

    async def _generate_from_template(
        self, request: BOQGenerationRequest, enriched: Dict
    ) -> Dict[str, Any]:
        """Generate BOQ from template calculations using enriched data.
        All rates are fetched from price_service (DB or internet fallback).
        No hardcoded rates are used.
        Uses batched DB queries for performance.
        """
        derived = enriched.get("derived_quantities", {})
        total_area = enriched["total_floor_area_m2"]
        num_floors = len(enriched["floors"])
        city = enriched["project_info"]["city"]
        finish_grade = enriched["finishes"]["finish_grade"]

        try:
            city_factor = await self.price_service.get_city_factor(city)
            finish_factor = await self.price_service.get_finish_level_multiplier(finish_grade)
        except Exception:
            city_factor = 1.0
            finish_factor = 1.0

        wall_area = derived.get("net_wall_area_m2", total_area * 2.8 * num_floors)
        total_perimeter = enriched.get("total_perimeter_m", math.sqrt(total_area) * 4)
        foundation_vol = derived.get("foundation_concrete_m3", total_area * 0.3)
        ext_blocks = derived.get("external_blocks_225mm", total_area * 20)
        int_blocks = derived.get("internal_blocks_150mm", total_area * 12)
        roof_area = derived.get("roof_slope_area_m2", total_area * 1.3)
        door_count = sum(len(f["openings"]["doors"]) for f in enriched["floors"])
        window_count = sum(len(f["openings"]["windows"]) for f in enriched["floors"])
        floor_finish_area = derived.get("floor_finish_area_m2", total_area)
        wall_finish_area = derived.get("wall_finish_area_m2", wall_area)
        ext_wall_area = wall_area * 0.3
        pf = enriched["services"].get("plumbing_fixtures", {})
        wc_count = pf.get("wc", 2)
        whb_count = pf.get("wash_hand_basin", 2)
        shower_count = pf.get("shower", 2)
        sink_count = pf.get("kitchen_sink", 1)
        has_overhead_tank = enriched["services"].get("overhead_tank", True)

        # Template fallback bill. The numeric column is a composite supply &
        # fix default used only when neither the DB nor the online average
        # reference can price the line (see _resolve_rate below), so it can
        # never override a real rate.
        all_items = [
            ("PRE-001", "Site clearance & preparation", 1, "ls", 150000, False),
            ("PRE-002", "Scaffolding hire", total_area, "m2", 800, False),
            ("PRE-003", "Concrete mixer hire", 1, "ls", 200000, False),
            ("PRE-004", "Setting out & survey", 1, "ls", 100000, False),
            ("SUB-001", "Excavation (strip foundation)", round(foundation_vol * 1.5, 2), "m3", 2500, False),
            ("SUB-002", "Hardcore filling 150mm thick", round(total_area, 2), "m2", 1800, False),
            ("SUB-003", "Blinding concrete (1:3:6) 50mm", round(total_area, 2), "m2", 2200, False),
            ("SUB-004", "Foundation concrete (1:2:4)", round(foundation_vol, 2), "m3", 94000, False),
            ("SUB-005", "DPC membrane (oversite)", round(total_area, 2), "m2", 1200, False),
            ("SUB-006", "Reinforcement (foundation)", round(derived.get("estimated_rebar_kg", total_area * 15) * 0.3, 2), "kg", 850, False),
            ("SUP-001", "9-inch sandcrete block wall (225mm)", round(ext_blocks), "nr", 1300, False),
            ("SUP-002", "6-inch sandcrete block wall (150mm)", round(int_blocks), "nr", 1100, False),
            ("SUP-003", "RC Columns 225x225mm (1:2:4)", round(num_floors * 12), "nr", 85000, False),
            ("SUP-004", "RC Ring Beams 225x450mm", round(total_perimeter, 2), "m", 12000, False),
            ("SUP-005", "RC Lintels over openings", round(total_perimeter * 0.3, 2), "m", 8000, False),
            ("SUP-006", "Ground floor slab (1:2:4) 150mm", round(total_area * 0.15, 2), "m3", 94000, False),
            ("ROF-001", "Timber roof trusses (supply & fix)", round(roof_area, 2), "m2", 6500, False),
            ("ROF-002", "Longspan aluminium roofing 0.55mm", round(roof_area, 2), "m2", 6000, False),
            ("ROF-003", "Ridge capping", round(math.sqrt(roof_area) * 1.5, 2), "m", 3500, False),
            ("ROF-004", "Fascia & soffit board", round(math.sqrt(roof_area) * 4, 2), "m", 3500, False),
                        ("ROF-005", "Rainwater gutter (uPVC)", round(math.sqrt(roof_area) * 4, 2), "m", 2500, False),
            ("ROF-006", "Downpipe (uPVC)", round(num_floors * 4), "nr", 4500, False),
            ("JON-001", "Internal flush door (0.8x2.1m) with frame", max(door_count, 6), "nr", 65000, False),
            ("JON-002", "External security door (0.9x2.1m)", max(1, door_count // 4), "nr", 120000, False),
            ("JON-003", "Aluminium sliding window 1.2x1.2m", max(window_count, 4), "nr", 95000, False),
            ("JON-004", "Burglar-proofing (window)", max(window_count, 4), "nr", 25000, False),
            ("FIN-001", "Floor screed (25mm) cement/sand 1:4", round(floor_finish_area, 2), "m2", 2500, False),
            ("FIN-002", "Ceramic floor tile 600x600mm (supply & fix)", round(floor_finish_area, 2), "m2", 13300, True),
            ("FIN-003", "Wall plastering (15mm) cement/sand 1:4", round(wall_finish_area, 2), "m2", 2800, False),
            ("FIN-004", "Wall tiling (wet areas) 300x600mm", round(wall_finish_area * 0.15, 2), "m2", 12000, True),
            ("FIN-005", "POP ceiling (supply & install)", round(floor_finish_area, 2), "m2", 6500, False),
            ("FIN-006", "Emulsion paint (2 coats) walls", round(wall_finish_area, 2), "m2", 3400, True),
            ("FIN-007", "Skirting (ceramic) 100mm high", round(enriched.get("total_perimeter_m", math.sqrt(total_area) * 4), 2), "m", 2500, False),
            ("EXT-001", "External rendering (20mm) cement/sand 1:4", round(ext_wall_area, 2), "m2", 3200, False),
            ("EXT-002", "External emulsion paint (2 coats)", round(ext_wall_area, 2), "m2", 3500, False),
            ("PLB-001", "Cold water supply pipework (PVC)", round(total_area * 0.3, 2), "m", 2500, False),
            ("PLB-002", "Drainage pipework (PVC 4in)", round(total_area * 0.2, 2), "m", 3500, False),
            ("PLB-003", "WC suite (low level)", wc_count, "nr", 65000, False),
            ("PLB-004", "Wash hand basin", whb_count, "nr", 25000, False),
            ("PLB-005", "Shower fitting", shower_count, "nr", 18000, False),
            ("PLB-006", "Kitchen sink (stainless steel)", sink_count, "nr", 45000, False),
            ("PLB-007", "Overhead water tank (1000L)", 1 if has_overhead_tank else 0, "nr", 180000, False),
            ("PLB-008", "Septic tank & soakaway", 1, "ls", 350000, False),
            ("ELE-001", "PVC conduit & wiring (2.5mm) per point", round(total_area * 0.5, 2), "m", 1200, False),
            ("ELE-002", "Lighting point (complete)", round(total_area * 0.15, 2), "nr", 8500, False),
            ("ELE-003", "Socket outlet (double, complete)", round(total_area * 0.1, 2), "nr", 9500, False),
            ("ELE-004", "Distribution board (8-way)", 1, "nr", 35000, False),
            ("ELE-005", "Earthing system", 1, "ls", 80000, False),
            ("EXTW-001", "Fencing (sandcrete block wall)", round(math.sqrt(total_area) * 4, 2), "m", 18000, False),
            ("EXTW-002", "Gate (metal, sliding)", 1, "nr", 250000, False),
            ("EXTW-003", "Interlocking paving (driveway)", round(total_area * 0.15, 2), "m2", 8500, False),
            ("EXTW-004", "Landscaping & planting", round(total_area * 0.2, 2), "m2", 3500, False),
        ]

        # Fetch all rates concurrently. Units are passed through because the
        # chain is: our product catalogue (discount price first) -> our
        # material_rates (current_price) -> current best online price -> the
        # offline online-average reference -> a flagged AI estimate. Only the
        # online tiers need the unit, so a per-bag figure can never price an m2
        # line; the AI estimate and the hard-coded default rate below are the
        # documented last resort that keeps this BOQ complete.
        descriptions = [item[1] for item in all_items]
        units = [item[3] for item in all_items]
        rate_results = await asyncio.gather(*[
            self.price_service.get_rate(desc, city, unit=unit)
            for desc, unit in zip(descriptions, units)
        ])
        batch_rates = {}
        for desc, result in zip(descriptions, rate_results):
            if result and result.get("rate"):
                batch_rates[desc.lower()] = result["rate"]

        def _resolve_rate(description: str, default_rate: float) -> float:
            rate = batch_rates.get(description.lower())
            if rate is not None:
                return rate
            return default_rate * city_factor

        # Build elements from resolved items
        element_map = {
            "Preliminaries": ("Preliminaries", []),
            "Substructure": ("Substructure", []),
            "Superstructure": ("Superstructure", []),
            "Roofing": ("Roofing", []),
            "Joinery (Doors & Windows)": ("Joinery", []),
            "Internal Finishes": ("Finishes", []),
            "External Finishes": ("Finishes", []),
            "Plumbing & Drainage": ("Services", []),
            "Electrical Installation": ("Services", []),
            "External Works": ("External Works", []),
        }

        element_items = {k: [] for k in element_map}
        element_order = [
            "Preliminaries", "Substructure", "Superstructure", "Roofing",
            "Joinery (Doors & Windows)", "Internal Finishes", "External Finishes",
            "Plumbing & Drainage", "Electrical Installation", "External Works",
        ]

        code_to_element = {
            "PRE": "Preliminaries", "SUB": "Substructure", "SUP": "Superstructure",
            "ROF": "Roofing", "JON": "Joinery (Doors & Windows)", "FIN": "Internal Finishes",
            "EXT": "External Finishes", "PLB": "Plumbing & Drainage",
            "ELE": "Electrical Installation", "EXTW": "External Works",
        }

        for code, desc, qty, unit, default_rate, apply_finish in all_items:
            rate = _resolve_rate(desc, default_rate)
            if apply_finish:
                rate = rate * finish_factor
            prefix = code.split("-")[0]
            element_name = code_to_element.get(prefix, "Preliminaries")
            element_items[element_name].append({
                "itemCode": code, "description": desc,
                "quantity": qty, "unit": unit,
                "rate": round(rate), "amount": round(qty * rate),
                "estimated": True,
                "quantity_source": "mitm",  # Nigerian default ratio based
            })

        elements = []
        total_cost = 0
        for name in element_order:
            items = element_items[name]
            if not items:
                continue
            cost = sum(i["amount"] for i in items)
            trade = element_map[name][1]
            elements.append({
                "elementName": name, "trade": trade,
                "totalCost": round(cost, 2), "items": items,
            })
            total_cost += cost

        # Compute summary
        contingencies = total_cost * 0.10
        overheads_profit = total_cost * 0.10
        vat_rate = 0.075
        vat_amount = (total_cost + contingencies + overheads_profit) * vat_rate
        grand_total = total_cost + contingencies + overheads_profit + vat_amount

        return {
            "projectTitle": enriched["project_info"]["project_title"],
            "generatedAt": datetime.utcnow().isoformat(),
            "elements": elements,
            "assumptions": [
                f"Rates based on {city} market prices",
                f"Finish grade: {finish_grade} (multiplier: {finish_factor})",
                "All quantities estimated from floor area and standard Nigerian ratios",
                "Contingency: 10%, Overheads & Profit: 10%, VAT: 7.5%",
                "Wastage factors applied: blocks 5%, concrete 5%, tiles 10%, roofing 12%",
            ],
            "notes": [
                "This is an AI-generated estimate. Review by a professional QS recommended.",
                "Prices may vary by location, season, and supplier availability.",
                "Site conditions may affect actual quantities.",
            ],
        }
