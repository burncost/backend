"""BOQ → cart resolution.

Turns the material lines of a generated BOQ into concrete catalogue offers from
verified vendors, ranked by availability, rating and effective price.

READ-ONLY: nothing is written here. The shopper's cart is written by the
existing `/cart/add` path via the frontend `cartStore.addToCart`, so this feature
adds no second write path to the cart and no new guest/merge logic.

Resolution reuses `ToolExecutor._get_cheapest_price`, so a BOQ line resolves
exactly like the same request typed into chat: same catalogue, same brand-token
retries, same marketer/market/MOQ provenance.
"""
from typing import Any, Dict, List, Optional, Tuple
import logging
import math
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.boq_generator import (
    _M2_TO_BLOCK_FACTOR,
    _PROVISIONAL_SUM_HINT,
    _normalise_unit,
)
from app.services.chat_service import ToolExecutor

logger = logging.getLogger(__name__)

# A supplier at or above this rating is preferred over a cheaper unrated one. It
# is a preference, not a gate: when a line only has lower-rated in-stock offers
# the best of them is used and the line says so, instead of dropping the item.
PREFERRED_VENDOR_RATING = 3.5

# How many catalogue offers to inspect per line, and how many alternates to
# return so the shopper can swap a vendor without another request.
OFFERS_PER_LINE = 8
ALTERNATES_PER_LINE = 3

# Element groups that are not materials — nothing to put in a cart.
_NON_MATERIAL_ELEMENTS = ("preliminar",)

# Work/allowance wording: a rate for doing the work, not a product to buy.
_NON_MATERIAL_HINTS = (
    "labour only",
    "supervision",
    "insurance",
    "scaffold",
    "hoarding",
    "site clearance",
    "site setup",
    "testing",
    "commissioning",
    "professional fee",
)

# Bill wording that is not part of the product name. Longest phrases first so
# "Provide and fix" is removed whole rather than leaving a stray "fix".
_LEADING_VERBS = re.compile(
    r"^(?:provide and fix|provide and install|supply and fix|supply and install|"
    r"laying of|erection of|provide|supply|install|fix|lay|erect)\s+(?:and\s+)?",
    re.I,
)
_PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^\]]*\]")


class BOQCartService:
    """Resolves a BOQ's purchasable lines to vendor offers (no writes)."""

    def __init__(self, db: AsyncSession):
        self.db = db
        # Same executor chat uses, so matching behaviour cannot drift.
        self.executor = ToolExecutor(db=db)


    async def resolve(
        self,
        boq: Dict[str, Any],
        city: str = "",
        limit_offers: int = ALTERNATES_PER_LINE,
    ) -> Dict[str, Any]:
        """Resolve every purchasable BOQ line to a verified vendor offer.

        Returns ready-to-add lines (with alternates), the lines no verified
        vendor can fill, and the lines that are not materials at all.
        """
        city = city or (boq.get("project_info") or {}).get("city") or "Abuja"

        lines: List[Dict[str, Any]] = []
        unmatched: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []

        for element in boq.get("elements") or []:
            element_name = str(
                element.get("element_name") or element.get("elementName") or ""
            )
            for raw in element.get("items") or []:
                item = self._shape_item(raw, element_name)

                reason = self._skip_reason(item, element_name)
                if reason:
                    skipped.append(
                        {
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "reason": reason,
                        }
                    )
                    continue

                line, miss = await self._resolve_line(item, city, limit_offers)
                if line:
                    lines.append(line)
                else:
                    unmatched.append({**item, "reason": miss})

        subtotal = round(sum(line["line_total"] for line in lines), 2)
        notes: List[str] = []
        if any("supplier minimum" in " ".join(l["reasons"]) for l in lines):
            notes.append(
                "Quantities below a supplier's minimum order quantity were raised "
                "to that minimum so the cart can be checked out."
            )
        if unmatched:
            notes.append(
                f"{len(unmatched)} line(s) had no verified in-stock vendor — review "
                "them before checkout."
            )
        if skipped:
            notes.append(
                f"{len(skipped)} line(s) are not materials (preliminaries or "
                "work-only rates) and were left out of the cart."
            )

        return {
            "city": city,
            "lines": lines,
            "unmatched": unmatched,
            "skipped": skipped,
            "summary": {
                "lines_ready": len(lines),
                "lines_unmatched": len(unmatched),
                "lines_skipped": len(skipped),
                "materials_subtotal": subtotal,
                "suppliers": sorted(
                    {str(l["match"].get("marketer") or "") for l in lines} - {""}
                ),
                "notes": notes,
            },
        }

    # ── Line shaping ─────────────────────────────────────────────────────────

    @staticmethod
    def _shape_item(raw: Dict[str, Any], element_name: str) -> Dict[str, Any]:
        return {
            "item_code": str(raw.get("item_code") or raw.get("itemCode") or ""),
            "description": str(raw.get("description") or ""),
            "unit": str(raw.get("unit") or ""),
            "quantity": float(raw.get("quantity") or 0),
            "element_name": element_name,
        }

    @staticmethod
    def _skip_reason(item: Dict[str, Any], element_name: str) -> Optional[str]:
        """Why this line is not something to buy, or None when it is."""
        lowered_element = (element_name or "").lower()
        if any(hint in lowered_element for hint in _NON_MATERIAL_ELEMENTS):
            return "preliminaries are not materials"

        description = (item["description"] or "").lower()
        if _PROVISIONAL_SUM_HINT.search(description):
            return "provisional sum / allowance — carried, not bought"
        for hint in _NON_MATERIAL_HINTS:
            if hint in description:
                return f"not a material line ({hint})"
        if item["quantity"] <= 0:
            return "no quantity to buy"
        return None

    @staticmethod
    def _search_term(description: str) -> str:
        """The product name hiding inside a bill line description."""
        term = _PARENTHETICAL.sub(" ", description or "").strip()
        # Loop because a bill line can stack verbs ("Provide and fix ...").
        for _ in range(3):
            stripped = _LEADING_VERBS.sub("", term).strip()
            if stripped == term:
                break
            term = stripped
        return re.sub(r"\s{2,}", " ", term).strip(" .,-")[:80]


    # ── Vendor selection ─────────────────────────────────────────────────────

    @staticmethod
    def _unit_factor(
        bill_unit: str, offer_unit: str, description: str
    ) -> Optional[float]:
        """Offer quantity = bill quantity x factor, or None when incompatible.

        Walling is the documented exception: the bill measures the wall in m²
        while the catalogue sells blocks by number, using the same 9.9 blocks/m²
        face factor the rate matching already applies. A mismatch is never
        guessed — the line is reported for review instead.
        """
        if not offer_unit or bill_unit == offer_unit:
            return 1.0
        if bill_unit == "m2" and offer_unit == "nr" and "block" in description.lower():
            return _M2_TO_BLOCK_FACTOR
        return None

    async def _resolve_line(
        self, item: Dict[str, Any], city: str, limit_offers: int
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        term = self._search_term(item["description"])
        if not term:
            return None, "description carries no product name"

        # only_verified=True: a checkout path must never bill a suspended vendor.
        found = await self.executor._get_cheapest_price(
            term, location=city, limit=OFFERS_PER_LINE, only_verified=True
        )
        offers = found.get("offers") or []
        if not offers:
            return None, f"no verified vendor lists '{term}'"

        bill_unit = _normalise_unit(item["unit"])
        candidates: List[Dict[str, Any]] = []
        unit_mismatch = False
        out_of_stock = False
        inactive = False

        for offer in offers:
            offer_unit = _normalise_unit(str(offer.get("unit") or ""))
            factor = self._unit_factor(bill_unit, offer_unit, item["description"])
            if factor is None:
                unit_mismatch = True
                continue
            # Only a live listing can be bought (cart.add_to_cart rejects others).
            if str(offer.get("status") or "").lower() != "active":
                inactive = True
                continue

            rate = float(offer.get("rate") or 0)
            if rate <= 0:
                continue

            required = math.ceil(item["quantity"] * factor)
            moq = int(offer.get("minimum_order_quantity") or 1)
            billable = max(required, moq)
            if int(offer.get("stock") or 0) < billable:
                out_of_stock = True
                continue

            shipping = float(offer.get("shipping_fee") or 0)
            candidates.append(
                {
                    **offer,
                    "unit_factor": factor,
                    "required_quantity": required,
                    "billable_quantity": billable,
                    "raised_to_minimum": billable > required,
                    "line_total": round(billable * rate + shipping, 2),
                }
            )

        if not candidates:
            if unit_mismatch:
                return None, "catalogue sells this in a different unit — check by hand"
            if out_of_stock:
                return None, "verified vendors are out of stock for the required quantity"
            if inactive:
                return None, "no active (verified) listing for this item"
            return None, f"no verified vendor lists '{term}'"

        # Preference: a well-rated supplier first, then the cheapest landed cost.
        candidates.sort(
            key=lambda o: (
                0 if float(o.get("rating") or 0) >= PREFERRED_VENDOR_RATING else 1,
                o["line_total"],
            )
        )
        chosen = candidates[0]

        reasons: List[str] = []
        if float(chosen.get("rating") or 0) < PREFERRED_VENDOR_RATING:
            reasons.append(
                f"no {PREFERRED_VENDOR_RATING}+ rated supplier for this line — the "
                "cheapest in-stock verified offer was used"
            )
        if chosen["raised_to_minimum"]:
            reasons.append(
                "raised to the supplier minimum of "
                f"{chosen.get('minimum_order_quantity')} {chosen.get('unit')}"
            )
        if not chosen.get("is_verified"):
            reasons.append("vendor listing is not badge-verified")
        if found.get("relaxed_from"):
            reasons.append(
                f"no exact catalogue match for '{found['relaxed_from']}' — closest "
                "available option"
            )

        return (
            {
                "item_code": item["item_code"],
                "description": item["description"],
                "unit": item["unit"],
                "quantity": item["quantity"],
                "matched_query": found.get("matched_query"),
                "match": self._offer_public(chosen),
                "alternates": [
                    self._offer_public(o)
                    for o in candidates[1 : 1 + max(0, limit_offers)]
                ],
                "line_total": chosen["line_total"],
                "reasons": reasons,
            },
            None,
        )

    @staticmethod
    def _offer_public(offer: Dict[str, Any]) -> Dict[str, Any]:
        """The subset of an offer the cart UI needs (keeps the payload small)."""
        return {
            "product_id": offer.get("product_id"),
            "product_name": offer.get("product_name"),
            "unit": offer.get("unit"),
            "unit_price": offer.get("rate"),
            "base_price": offer.get("base_price"),
            "discount_price": offer.get("discount_price"),
            "marketer": offer.get("marketer"),
            "market": offer.get("market"),
            "rating": offer.get("rating"),
            "review_count": offer.get("review_count"),
            "is_verified": offer.get("is_verified"),
            "stock": offer.get("stock"),
            "minimum_order_quantity": offer.get("minimum_order_quantity"),
            "shipping_fee": offer.get("shipping_fee"),
            "billable_quantity": offer.get("billable_quantity"),
            "line_total": offer.get("line_total"),
        }
