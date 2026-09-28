from __future__ import annotations

"""
ONLINE AVERAGE PRICE REFERENCE

Last-resort price source for the BOQ pipeline. It is consulted ONLY when our own
price database (`material_rates` in PostgreSQL/MongoDB, i.e. vendor/product
prices) has no match for a line item, so a contract sum can still be completed.

Rules enforced here (see tests/test_online_average_prices.py):
  * Strictly offline and deterministic - there is no network call at request
    time, so the pipeline can never break because a third-party listing is slow,
    rate-limited or unreachable.
  * It never second-guesses the database: callers must try the DB first
    (PriceTruthService / PriceService._load_prices) and only then fall through.
  * Provenance is explicit: price_source="online_average", and every surface
    labels the figure "Online average price". The `verified` flag is left blank
    (only our own database asserts it). The upstream listing this reference was
    gathered from is never named or linked in an API payload, a BOQ document or
    UI copy.
  * Unit-safe: a lookup only succeeds when the line item's unit is compatible
    with the way the material is actually sold, so an item measured in m2 is
    never priced with a per-bag rate. When the caller supplies no unit at all,
    the description itself must state the selling unit (e.g. "per tonne").

Figures are observed median prices (or the average of published medians across
brands) for a Lagos baseline, held in NGN, and are converted to the requested
city with the same CITY_FACTORS multipliers the database path uses.
"""
import re
from typing import Any, Dict, List, Optional

# Lagos sits at 1.05 in price_service.CITY_FACTORS, which is Abuja-relative.
# This reference is Lagos-based, so every rate is divided by that factor before
# the requested city's multiplier is applied (callers pass the factor in to
# avoid a circular import with price_service).
BASELINE_CITY_FACTOR = 1.05

PROMOTED_SOURCE = "online_average"
PROMOTED_LABEL = "Online average price"

# Canonical unit spellings, so "m²", "sq.m" and "M2" all resolve to "m2".
_UNIT_ALIASES: Dict[str, str] = {
    "m2": "m2", "m²": "m2", "m^2": "m2", "sqm": "m2", "sq.m": "m2", "sq m": "m2",
    "sq metre": "m2", "sq meter": "m2", "square metre": "m2", "square meter": "m2",
    "m3": "m3", "m³": "m3", "m^3": "m3", "cubic metre": "m3", "cubic meter": "m3",
    "m": "m", "lm": "m", "metre": "m", "meter": "m", "linear metre": "m",
    "nr": "nr", "no": "nr", "nos": "nr", "pc": "nr", "pcs": "nr",
    "piece": "nr", "pieces": "nr", "unit": "nr",
    "length": "length", "lengths": "length", "bar": "length",
    "tonne": "tonne", "tonnes": "tonne", "ton": "tonne", "t": "tonne",
    "kg": "kg", "kilogram": "kg",
    "bag": "bag", "bags": "bag",
    "block": "block", "blocks": "block",
    "bundle": "bundle", "bundles": "bundle",
    "roll": "100-yard roll", "rolls": "100-yard roll",
    "yard": "100-yard roll", "yards": "100-yard roll",
    "load": "5T load",
}

# Conversion factors from one unit of the material as sold to the unit a BOQ
# line may use: rate_in_target_unit = rate_per_source_unit * factor. Only
# defined where the physical conversion is standard, so a unit this reference
# does not understand can never be invented.
_KG_PER_12M_ROD: Dict[str, float] = {
    "10mm": 8.88, "12mm": 12.78, "16mm": 18.96, "20mm": 29.64, "25mm": 46.32,
}


def _rod_conversions(size: str) -> Dict[str, float]:
    """Per-tonne rod price -> per 12m length / per kg."""
    kg = _KG_PER_12M_ROD[size]
    return {"length": round(kg / 1000.0, 6), "kg": 0.001}


# ─────────────────────────────────────────
# REFERENCE TABLE (Lagos median, NGN)
# ─────────────────────────────────────────
ONLINE_AVERAGE_PRICES: List[Dict[str, Any]] = [
    # ── Cement ──
    {
        "name": "Cement 50kg bag",
        "unit": "bag",
        "price": 14_000.0,
        "category": "cement",
        "keywords": ["cement"],
        "spec": [],
        "conversions": {"tonne": 20.0},
        "unit_tokens": ["bag", "50kg", "tonne", "ton"],
        "observed": "2026-09",
    },
    # ── Aggregates (sand is sold by the load) ──
    {
        "name": "Sharp sand",
        "unit": "5T load",
        "price": 70_000.0,
        "category": "aggregate",
        "keywords": ["sharp sand"],
        "spec": [],
        # 5 tonnes of sharp sand is about 3.125 m3 at ~1.6 t/m3.
        "conversions": {"m3": 0.32, "tonne": 0.2},
        "unit_tokens": ["m3", "tonne", "load", "trip", "5t"],
        "observed": "2026-09",
    },
    {
        "name": "Plaster sand",
        "unit": "5T load",
        "price": 90_000.0,
        "category": "aggregate",
        "keywords": ["plaster sand"],
        "spec": [],
        "conversions": {"m3": 0.32, "tonne": 0.2},
        "unit_tokens": ["m3", "tonne", "load", "trip", "5t"],
        "observed": "2026-09",
    },
    {
        "name": "Sand (unspecified)",
        "unit": "5T load",
        "price": 70_000.0,
        "category": "aggregate",
        "keywords": ["sand"],
        "spec": [],
        "conversions": {"m3": 0.32, "tonne": 0.2},
        "unit_tokens": ["m3", "tonne", "load", "trip", "5t"],
        "observed": "2026-09",
    },
    # ── Blocks ──
    {
        "name": "Sandcrete block 225mm (9 inch)",
        "unit": "block",
        "price": 1_000.0,
        "category": "block",
        "keywords": ["block", "sandcrete"],
        "spec": ["225mm", "9 inch", "9inch"],
        "conversions": {"nr": 1.0},
        "unit_tokens": ["block", "nr"],
        "observed": "2026-09",
    },
    {
        "name": "Sandcrete block 150mm (6 inch)",
        "unit": "block",
        "price": 700.0,
        "category": "block",
        "keywords": ["block", "sandcrete"],
        "spec": ["150mm", "6 inch", "6inch"],
        "conversions": {"nr": 1.0},
        "unit_tokens": ["block", "nr"],
        "observed": "2026-09",
    },
    {
        "name": "Sandcrete block 100mm (4 inch)",
        "unit": "block",
        "price": 850.0,
        "category": "block",
        "keywords": ["block", "sandcrete"],
        "spec": ["100mm", "4 inch", "4inch"],
        "conversions": {"nr": 1.0},
        "unit_tokens": ["block", "nr"],
        "observed": "2026-09",
    },
    # ── Reinforcement (sold per tonne) ──
    {
        "name": "Steel rod TMT 10mm",
        "unit": "tonne",
        "price": 1_180_000.0,
        "category": "rebar",
        "keywords": ["steel rod", "rod", "rebar", "reinforcement", "tmt"],
        "spec": ["10mm", "y10"],
        "conversions": _rod_conversions("10mm"),
        "unit_tokens": ["tonne", "ton", "length", "kg"],
        "observed": "2026-09",
    },
    {
        "name": "Steel rod TMT 12mm",
        "unit": "tonne",
        "price": 1_180_000.0,
        "category": "rebar",
        "keywords": ["steel rod", "rod", "rebar", "reinforcement", "tmt"],
        "spec": ["12mm", "y12"],
        "conversions": _rod_conversions("12mm"),
        "unit_tokens": ["tonne", "ton", "length", "kg"],
        "observed": "2026-09",
    },
    {
        "name": "Steel rod TMT 16mm",
        "unit": "tonne",
        "price": 1_220_000.0,
        "category": "rebar",
        "keywords": ["steel rod", "rod", "rebar", "reinforcement", "tmt"],
        "spec": ["16mm", "y16"],
        "conversions": _rod_conversions("16mm"),
        "unit_tokens": ["tonne", "ton", "length", "kg"],
        "observed": "2026-09",
    },
    {
        "name": "Steel rod TMT 20mm",
        "unit": "tonne",
        "price": 1_200_000.0,
        "category": "rebar",
        "keywords": ["steel rod", "rod", "rebar", "reinforcement", "tmt"],
        "spec": ["20mm", "y20"],
        "conversions": _rod_conversions("20mm"),
        "unit_tokens": ["tonne", "ton", "length", "kg"],
        "observed": "2026-09",
    },
    {
        "name": "Steel rod TMT 25mm",
        "unit": "tonne",
        "price": 1_200_000.0,
        "category": "rebar",
        "keywords": ["steel rod", "rod", "rebar", "reinforcement", "tmt"],
        "spec": ["25mm", "y25"],
        "conversions": _rod_conversions("25mm"),
        "unit_tokens": ["tonne", "ton", "length", "kg"],
        "observed": "2026-09",
    },
    # ── Roofing ──
    {
        "name": "Aluminium roofing sheet (long span) 0.55mm",
        "unit": "m2",
        "price": 7_500.0,
        "category": "roofing",
        "keywords": ["aluminium roofing", "long span", "longspan", "roofing sheet"],
        "spec": ["0.55mm", "0.55 mm"],
        "conversions": {},
        "unit_tokens": ["m2", "sq m", "sqm"],
        "observed": "2026-09",
    },
    {
        "name": "Aluminium roofing sheet (long span) 0.45mm",
        "unit": "m2",
        "price": 5_700.0,
        "category": "roofing",
        "keywords": ["aluminium roofing", "long span", "longspan", "roofing sheet"],
        "spec": ["0.45mm", "0.45 mm"],
        "conversions": {},
        "unit_tokens": ["m2", "sq m", "sqm"],
        "observed": "2026-09",
    },
    # ── Timber ──
    {
        "name": "Plank Hardwood, 2x4 inch",
        "unit": "nr",
        "price": 1_900.0,
        "category": "timber",
        "keywords": ["plank", "hardwood", "timber"],
        "spec": ["2x4", "2 x 4"],
        "conversions": {},
        "unit_tokens": ["piece", "nr", "length"],
        "observed": "2026-09",
    },
    {
        "name": "Plank Hardwood, 2x3 inch",
        "unit": "nr",
        "price": 1_400.0,
        "category": "timber",
        "keywords": ["plank", "hardwood", "timber"],
        "spec": ["2x3", "2 x 3"],
        "conversions": {},
        "unit_tokens": ["piece", "nr", "length"],
        "observed": "2026-09",
    },
    {
        "name": "Plank Hardwood, 2x6 inch",
        "unit": "nr",
        "price": 3_500.0,
        "category": "timber",
        "keywords": ["plank", "hardwood", "timber"],
        "spec": ["2x6", "2 x 6"],
        "conversions": {},
        "unit_tokens": ["piece", "nr", "length"],
        "observed": "2026-09",
    },
    {
        "name": "Plank Softwood, 2x3 inch",
        "unit": "nr",
        "price": 1_300.0,
        "category": "timber",
        "keywords": ["plank", "softwood", "timber"],
        "spec": ["2x3", "2 x 3"],
        "conversions": {},
        "unit_tokens": ["piece", "nr", "length"],
        "observed": "2026-09",
    },
    {
        "name": "Plywood Veneer, 1/4 inch",
        "unit": "nr",
        "price": 8_500.0,
        "category": "timber",
        "keywords": ["plywood"],
        "spec": ["1/4", "quarter inch"],
        "conversions": {},
        "unit_tokens": ["piece", "nr", "sheet"],
        "observed": "2026-09",
    },
    {
        "name": "Plywood Veneer, 1/2 inch",
        "unit": "nr",
        "price": 16_500.0,
        "category": "timber",
        "keywords": ["plywood"],
        "spec": ["1/2", "half inch"],
        "conversions": {},
        "unit_tokens": ["piece", "nr", "sheet"],
        "observed": "2026-09",
    },
    # -- Electrical (cable is sold by the 100-yard roll) --
    {
        "name": "Cable 1.5mm (100-yard roll)",
        "unit": "100-yard roll",
        "price": 30_900.0,
        "category": "electrical",
        "keywords": ["cable"],
        "spec": ["1.5mm"],
        "conversions": {"m": 0.010936},
        "unit_tokens": ["roll", "yard", "m"],
        "observed": "2026-09",
    },
    {
        "name": "Cable 2.5mm (100-yard roll)",
        "unit": "100-yard roll",
        "price": 51_500.0,
        "category": "electrical",
        "keywords": ["cable"],
        "spec": ["2.5mm"],
        "conversions": {"m": 0.010936},
        "unit_tokens": ["roll", "yard", "m"],
        "observed": "2026-09",
    },
    {
        "name": "Cable 4mm (100-yard roll)",
        "unit": "100-yard roll",
        "price": 82_500.0,
        "category": "electrical",
        "keywords": ["cable"],
        "spec": ["4mm"],
        "conversions": {"m": 0.010936},
        "unit_tokens": ["roll", "yard", "m"],
        "observed": "2026-09",
    },
    {
        "name": "Cable 6mm (100-yard roll)",
        "unit": "100-yard roll",
        "price": 130_850.0,
        "category": "electrical",
        "keywords": ["cable"],
        "spec": ["6mm"],
        "conversions": {"m": 0.010936},
        "unit_tokens": ["roll", "yard", "m"],
        "observed": "2026-09",
    },
    {
        "name": "Cable 10mm (100-yard roll)",
        "unit": "100-yard roll",
        "price": 209_400.0,
        "category": "electrical",
        "keywords": ["cable"],
        "spec": ["10mm"],
        "conversions": {"m": 0.010936},
        "unit_tokens": ["roll", "yard", "m"],
        "observed": "2026-09",
    },
    {
        "name": "Cable 16mm (100-yard roll)",
        "unit": "100-yard roll",
        "price": 326_500.0,
        "category": "electrical",
        "keywords": ["cable"],
        "spec": ["16mm"],
        "conversions": {"m": 0.010936},
        "unit_tokens": ["roll", "yard", "m"],
        "observed": "2026-09",
    },
    {
        "name": "Conduit pipe UPVC 20mm",
        "unit": "bundle",
        "price": 10_000.0,
        "category": "electrical",
        "keywords": ["conduit"],
        "spec": ["20mm"],
        "conversions": {},
        "unit_tokens": ["bundle"],
        "observed": "2026-09",
    },
    {
        "name": "Conduit pipe UPVC 25mm",
        "unit": "bundle",
        "price": 14_000.0,
        "category": "electrical",
        "keywords": ["conduit"],
        "spec": ["25mm"],
        "conversions": {},
        "unit_tokens": ["bundle"],
        "observed": "2026-09",
    },
    {
        "name": "Distribution board 8-way",
        "unit": "nr",
        "price": 21_000.0,
        "category": "electrical",
        "keywords": ["distribution board", "db board"],
        "spec": [],
        "conversions": {},
        "unit_tokens": ["nr", "piece", "no"],
        "observed": "2026-09",
    },
    {
        "name": "ELCB 40A 30mA",
        "unit": "nr",
        "price": 10_250.0,
        "category": "electrical",
        "keywords": ["elcb"],
        "spec": [],
        "conversions": {},
        "unit_tokens": ["nr", "piece", "no"],
        "observed": "2026-09",
    },
    {
        "name": "MCB 1P 32A",
        "unit": "nr",
        "price": 3_900.0,
        "category": "electrical",
        "keywords": ["mcb", "circuit breaker"],
        "spec": [],
        "conversions": {},
        "unit_tokens": ["nr", "piece", "no"],
        "observed": "2026-09",
    },
    {
        "name": "Socket outlet 13A twin",
        "unit": "nr",
        "price": 1_725.0,
        "category": "electrical",
        "keywords": ["socket outlet", "socket"],
        "spec": [],
        "conversions": {},
        "unit_tokens": ["nr", "piece", "no", "point"],
        "observed": "2026-09",
    },
    {
        "name": "Switch 2-gang 1-way",
        "unit": "nr",
        "price": 812.0,
        "category": "electrical",
        "keywords": ["switch"],
        "spec": ["2-gang", "2 gang", "2gang"],
        "conversions": {},
        "unit_tokens": ["nr", "piece", "no", "point"],
        "observed": "2026-09",
    },
]

_TOKEN_PATTERNS: Dict[str, "re.Pattern[str]"] = {}


def _normalize(text: str) -> str:
    """Lowercase, unify multiplication signs and collapse whitespace."""
    if not text:
        return ""
    cleaned = str(text).lower().replace("×", "x").replace("\u2013", "-")
    return re.sub(r"\s+", " ", cleaned).strip()


def _has_token(text: str, token: str) -> bool:
    """True when `token` appears as a whole token (so 'sand' != 'sandcrete')."""
    token = _normalize(token)
    if not token:
        return False
    pattern = _TOKEN_PATTERNS.get(token)
    if pattern is None:
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])")
        _TOKEN_PATTERNS[token] = pattern
    return bool(pattern.search(text))


def normalize_unit(unit: Optional[str]) -> Optional[str]:
    """Map a BOQ unit spelling onto the canonical unit used by this table."""
    if not unit:
        return None
    key = _normalize(unit).rstrip(".")
    return _UNIT_ALIASES.get(key, key)


def _unit_factor(entry: Dict[str, Any], unit: Optional[str], text: str) -> Optional[float]:
    """
    Factor converting the entry's rate onto the line item's unit.

    None means "units are incompatible", which is what stops a per-bag rate from
    being applied to a line measured in m2. When the caller supplies no unit at
    all, the description itself must state the selling unit.
    """
    conversions: Dict[str, float] = entry.get("conversions") or {}
    canonical = normalize_unit(unit)

    if canonical:
        if canonical == entry["unit"]:
            return 1.0
        return conversions.get(canonical)

    if any(_has_token(text, token) for token in entry.get("unit_tokens", [])):
        return 1.0
    return None


def find_online_average(
    description: str,
    city: str = "Abuja",
    unit: Optional[str] = None,
    city_factor: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Online average price for a line item, or None when nothing here matches its
    description/unit (callers then fall through to the next source).

    `city_factor` is price_service.CITY_FACTORS.get(city, 1.0), passed in by the
    caller so this module keeps no import from price_service.
    """
    text = _normalize(description)
    if not text:
        return None

    best_entry: Optional[Dict[str, Any]] = None
    best_score = 0
    best_factor = 1.0

    for entry in ONLINE_AVERAGE_PRICES:
        keyword_score = max(
            (len(_normalize(kw)) for kw in entry["keywords"] if _has_token(text, kw)),
            default=0,
        )
        if not keyword_score:
            continue

        spec = entry.get("spec") or []
        if spec:
            spec_score = max(
                (len(_normalize(token)) for token in spec if _has_token(text, token)),
                default=0,
            )
            # A size/spec match outranks a bare category word, so "cable 2.5mm"
            # can never be priced from the 1.5mm row.
            if not spec_score:
                continue
            keyword_score += spec_score

        factor = _unit_factor(entry, unit, text)
        if factor is None:
            continue

        if keyword_score > best_score:
            best_entry, best_score, best_factor = entry, keyword_score, factor

    if best_entry is None:
        return None

    factor = city_factor if city_factor and city_factor > 0 else 1.0
    rate = round(best_entry["price"] * best_factor * (factor / BASELINE_CITY_FACTOR), 2)
    if rate <= 0:
        return None

    return {
        "rate": rate,
        "unit": normalize_unit(unit) or best_entry["unit"],
        "product_name": f"{best_entry['name']} (online average)",
        "product_code": "",
        "price_source": PROMOTED_SOURCE,
        "source": PROMOTED_SOURCE,
        "confidence": 0.55,
        "city": city,
        "online_average": True,
        "price_basis": PROMOTED_LABEL,
        "observed": best_entry.get("observed"),
        "category": best_entry.get("category", ""),
    }

