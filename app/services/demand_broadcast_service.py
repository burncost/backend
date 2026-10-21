"""
BOQ-triggered vendor demand broadcasts.

Single choke-point that turns unpriced / out-of-stock BOQ lines into vendor
demand. It reuses the existing `DemandAlert` public feed for the line itself and
opens one per-vendor bid thread per matched vendor by reusing `Negotiation` +
`NegotiationCounterOffer` (the counter-offer table doubles as the thread's
message log). A `Notification` nudges each matched vendor.

Reuse strategy (Option A): no new tables are introduced, so the marketplace
demand feed and the negotiation centre keep working unchanged. If this ever
needs dedicated tables (Option B), only `_open_bid_thread` / `_post_bid_message`
have to change - every caller talks to `broadcast_boq_demand` / `place_vendor_bid`.

All helpers are best-effort: a single failing line or vendor is logged and
skipped, so a BOQ run never fails because of demand broadcasting.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models.demand_alert import DemandAlert
from app.models.negotiation import Negotiation, NegotiationCounterOffer
from app.models.notification import Notification
from app.models.product import Product, ProductStatus
from app.models.vendor import Vendor

logger = logging.getLogger(__name__)

# Bounds so a 300-line bill cannot fan out into hundreds of threads/rows.
_MAX_BID_LINES = 40
_MAX_VENDORS_PER_LINE = 8
# Vendors whose storefront is effectively closed must never be broadcast to.
_EXCLUDED_VENDOR_STATUS = ("suspended", "deactivated", "rejected")


def _coerce_uuid(value: Any) -> Optional[uuid.UUID]:
    """Accept a UUID or a string UUID; anything else becomes None (never raise)."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


# Material keys are the contract between the take-off engine and the vendor
# demand feed; the spec is what a vendor needs on the line to quote accurately.
_SPEC_BY_KEY: Dict[str, str] = {
    "blocks_225": "225mm",
    "blocks_150": "150mm",
    "floor_tiles_600": "600x600mm",
    "floor_tiles_400": "400x400mm",
    "wall_tiles_300x600": "300x600mm",
    "roof_sheets_longspan": "0.55mm aluminium",
    "roof_tiles_stone": "stone coated",
    "roof_tiles_concrete": "concrete",
    "roof_trusses": "timber",
    "roof_purlins": "timber",
    "roof_rafters": "timber",
}

# Vendor/product matching vocabulary. A take-off line says "9-inch sandcrete
# block (225mm)"; the catalogue says "Sandcrete Block 225mm" or "9 inch block".
# Expanding each recognised token to its whole synonym set stops a correct
# product being missed because the two texts choose different words.
_SYNONYM_GROUPS = (
    (
        "block", "blocks", "sandcrete", "blockwork", "9-inch", "6-inch",
        "9inch", "6inch", "225mm", "150mm", "225", "150",
    ),
    ("tile", "tiles", "ceramic", "porcelain", "vitrified", "floor tile", "wall tile"),
    ("roof", "roofing", "longspan", "aluminium", "aluminum", "sheet", "sheets", "roofing sheet"),
    ("truss", "trusses", "purlin", "purlins", "rafter", "rafters", "timber", "wood"),
    ("door", "doors", "flush door", "panel door", "security door"),
    ("window", "windows", "casement", "sliding window", "glazing", "glass"),
    ("cement", "opc", "portland", "bag"),
    ("paint", "emulsion", "gloss", "primer"),
    ("rebar", "reinforcement", "rod", "iron", "steel"),
    ("pipe", "pipes", "pvc", "plumbing"),
    ("cable", "wire", "wiring", "electrical"),
)


def _match_terms(description: str, limit: int = 12) -> List[str]:
    """Search terms for a line: its own words plus synonyms of recognised ones.

    The limit is generous on purpose: the block group alone needs its dimension
    spellings ("225mm", "9-inch", "9inch") as well as its product words, and
    dropping the dimension is exactly what made correct products unmatchable.
    """
    text = (description or "").lower()
    words = set(re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text))
    terms: List[str] = []
    for group in _SYNONYM_GROUPS:
        if any(term in text for term in group):
            for term in group:
                if term not in terms:
                    terms.append(term)
    for word in sorted(words, key=len, reverse=True):
        if word not in terms:
            terms.append(word)
    return [t for t in terms if len(t) > 2][:limit]


def material_demand_lines(
    derived: Optional[Dict[str, Any]], city: str = "Abuja"
) -> List[Dict[str, Any]]:
    """Turn the MITM `material_counts` take-off into broadcastable demand lines.

    Zero-quantity rows are dropped: a vendor cannot bid on "0 boxes".
    """
    lines: List[Dict[str, Any]] = []
    for row in (derived or {}).get("material_counts") or []:
        try:
            quantity = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue
        key = str(row.get("material_key") or "")
        lines.append({
            "material_key": key,
            "description": str(row.get("description") or "").strip(),
            "spec": _SPEC_BY_KEY.get(key),
            "quantity": quantity,
            "unit": str(row.get("unit") or "").strip(),
            "city": city,
            "source_ref": f"takeoff:{key}" if key else None,
        })
    return lines[:_MAX_BID_LINES]


def _dedupe_key(line: Dict[str, Any], city: str) -> str:
    """One open alert per (city, material). Quantities are refreshed in place."""
    identity = line.get("material_key") or re.sub(
        r"[^a-z0-9]+", "-", str(line.get("description") or "").lower()
    ).strip("-")
    return f"{(city or 'Abuja').strip().lower()}:{identity}"[:255]


def normalise_line(line: Dict[str, Any], default_city: str = "Abuja") -> Dict[str, Any]:
    """Flatten the several line shapes BOQ generation/verification emit into one."""
    quantity = line.get("quantity") or line.get("qty") or 0
    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        quantity = 0.0
    city = (line.get("city") or default_city or "Abuja").strip() or "Abuja"
    description = (line.get("description") or line.get("item_description") or "").strip()
    normalised = {
        "description": description,
        "quantity": quantity,
        "unit": (line.get("unit") or "").strip(),
        "city": city,
        "material_key": line.get("material_key"),
        "spec": line.get("spec"),
        "source_ref": line.get("source_ref"),
    }
    normalised["dedupe_key"] = _dedupe_key(normalised, city)
    return normalised



def _seed_message(line: Dict[str, Any]) -> str:
    qty = line.get("quantity") or 0
    unit = line.get("unit") or ""
    text = f"This builder may need {qty:g} {unit} of {line['description']} \u2014 place a bid with your best price."
    return " ".join(text.split())


async def _create_alert(
    pg_db: AsyncSession,
    line: Dict[str, Any],
    project_title: str,
    requested_by: Optional[uuid.UUID],
) -> Optional[DemandAlert]:
    alert = DemandAlert(
        item_description=line["description"],
        city=line["city"],
        quantity_needed=line["quantity"],
        unit=line["unit"],
        project_title=project_title or None,
        requested_by=requested_by,
        status="pending",
        material_key=line.get("material_key"),
        spec=line.get("spec"),
        source_ref=line.get("source_ref"),
        dedupe_key=line.get("dedupe_key"),
    )
    pg_db.add(alert)
    await pg_db.flush()
    return alert


async def _open_alert_for(
    pg_db: AsyncSession, requester: uuid.UUID, line: Dict[str, Any]
) -> Optional[DemandAlert]:
    """The builder's still-open alert for this material, if one exists.

    One alert per (builder, city, material) is the whole idempotency story: a
    regenerated bill refreshes the quantity instead of creating a second
    broadcast, and vendors keep the thread they already bid on.
    """
    if not line.get("dedupe_key"):
        return None
    return (
        await pg_db.execute(
            select(DemandAlert)
            .where(
                DemandAlert.requested_by == requester,
                DemandAlert.dedupe_key == line["dedupe_key"],
                or_(
                    DemandAlert.status.is_(None),
                    DemandAlert.status.notin_(("closed", "agreed", "expired")),
                ),
            )
            .order_by(DemandAlert.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


async def _match_vendors(
    pg_db: AsyncSession, description: str, limit: int = _MAX_VENDORS_PER_LINE
) -> Sequence[Vendor]:
    """Vendors with an active product whose text matches the line's keywords.

    Every recognised synonym is searched (OR), not just the longest token, so
    "9-inch sandcrete block wall" still finds a "Sandcrete Block 225mm" product.
    """
    terms = _match_terms(description)
    if not terms:
        return []
    patterns = [f"%{term}%" for term in terms]
    stmt = (
        select(Vendor)
        .join(Product, Product.vendor_id == Vendor.id)
        .where(
            Product.status == ProductStatus.ACTIVE,
            func.coalesce(Vendor.verification_status, "pending")
            .notin_(_EXCLUDED_VENDOR_STATUS),
            or_(*[
                clause
                for pattern in patterns
                for clause in (
                    func.lower(Product.name).like(pattern),
                    func.lower(func.coalesce(Product.short_description, "")).like(pattern),
                    func.lower(func.coalesce(Product.description, "")).like(pattern),
                    func.lower(func.coalesce(Product.meta_keywords, "")).like(pattern),
                )
            ]),
        )
        .distinct()
        .limit(limit)
    )
    return (await pg_db.execute(stmt)).scalars().all()


async def _next_negotiation_number(pg_db: AsyncSession, prefix: str) -> str:
    count = (await pg_db.execute(select(func.count(Negotiation.id)))).scalar() or 0
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d')}-{count + 1:06d}"


async def _post_bid_message(
    pg_db: AsyncSession,
    negotiation: Negotiation,
    *,
    side: str,
    note: str,
    discount_percent: float = 0,
    final_price: Optional[float] = None,
    product_id: Optional[uuid.UUID] = None,
    product_snapshot: Optional[Dict[str, Any]] = None,
) -> NegotiationCounterOffer:
    """Append a message to a bid thread (reuses the counter-offer table).

    `final_price` is what makes a message acceptable: an accept endpoint always
    reads the newest thread entry that carries a price, so a chat-only note can
    never be mistaken for an offer.
    """
    offer = NegotiationCounterOffer(
        negotiation_id=negotiation.id,
        offered_by=side,
        discount_percent=discount_percent,
        note=note,
        final_price=final_price,
        product_id=product_id,
        product_snapshot=product_snapshot,
    )
    pg_db.add(offer)
    return offer


def product_snapshot(product: Product, image_url: Optional[str] = None) -> Dict[str, Any]:
    """Immutable copy of what the vendor offered, safe to store on an offer row."""
    return {
        "id": str(product.id),
        "name": product.name,
        "sku": product.sku,
        "vendor_id": str(product.vendor_id),
        "category_id": str(product.category_id) if product.category_id else None,
        "base_price": float(product.base_price) if product.base_price is not None else None,
        "unit": product.unit_of_measure or "piece",
        "minimum_order_quantity": product.minimum_order_quantity or 1,
        "image_url": image_url,
    }


async def _primary_image_url(pg_db: AsyncSession, product_id: uuid.UUID) -> Optional[str]:
    """First image for a product; products have no image column of their own."""
    from app.models.product import ProductImage

    return (
        await pg_db.execute(
            select(ProductImage.image_url)
            .where(ProductImage.product_id == product_id)
            .order_by(ProductImage.is_primary.desc(), ProductImage.display_order.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def attach_product_to_negotiation(
    pg_db: AsyncSession,
    negotiation: Negotiation,
    *,
    vendor_id: uuid.UUID,
    product_id: uuid.UUID,
    price: Optional[float] = None,
    note: str = "",
    side: str = "supplier",
) -> NegotiationCounterOffer:
    """Attach a catalogue product (and optionally its price) to a bid thread.

    The offer is the point of the attachment: it carries the product snapshot and,
    when the vendor quoted one, the per-unit figure the buyer can accept. The
    product must belong to the vendor asking - otherwise one vendor could quote
    another's catalogue item.
    """
    product = (
        await pg_db.execute(select(Product).where(Product.id == product_id))
    ).scalars().first()
    if product is None:
        raise ValueError("Product not found")
    if product.vendor_id != vendor_id:
        raise ValueError("That product belongs to another vendor")
    if product.status != ProductStatus.ACTIVE:
        raise ValueError("That product is not active in the catalogue")

    unit_price = price
    if unit_price is None and product.base_price is not None:
        unit_price = float(product.base_price)
    if unit_price is not None:
        try:
            unit_price = float(unit_price)
        except (TypeError, ValueError):
            raise ValueError("price must be a number")
        if unit_price <= 0:
            raise ValueError("price must be greater than zero")

    snapshot = product_snapshot(product, await _primary_image_url(pg_db, product.id))
    negotiation.product_id = product.id
    negotiation.product_name = product.name
    negotiation.unit = product.unit_of_measure or negotiation.unit
    if unit_price is not None:
        negotiation.bid_price = unit_price
        negotiation.status = "counter_offered"

    base_price = float(product.base_price) if product.base_price is not None else None
    implied = None
    if base_price and unit_price is not None and base_price > 0:
        implied = round((base_price - unit_price) / base_price * 100, 2)

    offer = await _post_bid_message(
        pg_db,
        negotiation,
        side=side,
        note=note or f"Attached {product.name}" + (
            f" at {unit_price:,.2f} per {product.unit_of_measure or 'unit'}"
            if unit_price is not None else ""
        ),
        discount_percent=implied if implied is not None else 0,
        final_price=unit_price,
        product_id=product.id,
        product_snapshot=snapshot,
    )
    return offer


async def _open_bid_thread(
    pg_db: AsyncSession,
    alert: DemandAlert,
    vendor: Vendor,
    line: Dict[str, Any],
) -> bool:
    """Open a bid thread for (alert, vendor) once. Returns True if newly opened."""
    existing = (
        await pg_db.execute(
            select(Negotiation).where(
                Negotiation.demand_alert_id == alert.id,
                Negotiation.supplier_id == vendor.id,
            )
        )
    ).scalars().first()
    if existing is not None:
        return False

    negotiation = Negotiation(
        negotiation_number=await _next_negotiation_number(pg_db, "BID"),
        builder_id=alert.requested_by,
        supplier_id=vendor.id,
        product_name=alert.item_description,
        quantity=line.get("quantity") or 1,
        unit=line.get("unit") or "piece",
        requested_discount=0,
        value=0,
        status="pending",
        demand_alert_id=alert.id,
    )
    pg_db.add(negotiation)
    await pg_db.flush()
    await _post_bid_message(pg_db, negotiation, side="builder", note=_seed_message(line))

    if vendor.user_id:
        pg_db.add(
            Notification(
                user_id=vendor.user_id,
                type="demand",
                title="New demand near you",
                message=_seed_message(line),
                link="/supplier-dashboard?tab=demand-alerts",
            )
        )
    return True


async def broadcast_boq_demand(
    pg_db: Optional[AsyncSession],
    lines: Optional[List[Dict[str, Any]]],
    *,
    project_title: str = "",
    city: str = "Abuja",
    requested_by: Any = None,
    match_vendors: bool = True,
) -> Dict[str, Any]:
    """Broadcast BOQ lines as demand.

    Creates one `DemandAlert` per line, then a per-vendor bid thread + notification
    for each matched vendor. Returns a small summary dict (best-effort).
    """
    summary = {"alerts_created": 0, "alerts_updated": 0, "bids_opened": 0, "notified": 0}
    if pg_db is None:
        return summary

    requester = _coerce_uuid(requested_by)
    # Guests must never create alerts: there is no owner to negotiate with, so
    # the row would be permanent junk in every vendor's feed.
    if requester is None:
        return summary

    normalised = [normalise_line(line, city) for line in (lines or [])]
    normalised = [line for line in normalised if line["description"]][:_MAX_BID_LINES]
    if not normalised:
        return summary

    for line in normalised:
        try:
            existing = await _open_alert_for(pg_db, requester, line)
            if existing is not None:
                # Keep the open broadcast in step with the latest take-off
                # instead of raising a duplicate. Threads are untouched.
                if line["quantity"] and float(existing.quantity_needed or 0) != line["quantity"]:
                    existing.quantity_needed = line["quantity"]
                    summary["alerts_updated"] += 1
                continue
            alert = await _create_alert(pg_db, line, project_title, requester)
            if alert is None:
                continue
            summary["alerts_created"] += 1
            if not match_vendors:
                continue
            vendors = await _match_vendors(pg_db, line["description"])
            for vendor in vendors:
                try:
                    if await _open_bid_thread(pg_db, alert, vendor, line):
                        summary["bids_opened"] += 1
                        summary["notified"] += 1
                except Exception as exc:  # one bad vendor must not kill the line
                    logger.warning("Failed to open bid thread for '%s': %s", line["description"], exc)
        except Exception as exc:
            logger.warning("Failed to broadcast demand for '%s': %s", line.get("description"), exc)

    try:
        await pg_db.commit()
    except Exception as exc:
        logger.warning("Failed to commit demand broadcast: %s", exc)

    if summary["alerts_created"]:
        logger.info(
            "Demand broadcast: %s alerts, %s bid threads",
            summary["alerts_created"],
            summary["bids_opened"],
        )
    return summary


async def place_vendor_bid(
    pg_db: AsyncSession,
    alert: DemandAlert,
    vendor: Vendor,
    *,
    bid_price: float,
    note: str = "",
    product_id: Any = None,
) -> Negotiation:
    """Vendor places/updates a bid on a demand alert. Opens the thread if needed.

    `product_id` (optional) attaches one of the vendor's catalogue products to the
    quote, so the buyer can see and buy the exact item being offered.
    """
    negotiation = (
        await pg_db.execute(
            select(Negotiation)
            .options(selectinload(Negotiation.counter_offers))
            .where(
                Negotiation.demand_alert_id == alert.id,
                Negotiation.supplier_id == vendor.id,
            )
        )
    ).scalars().first()

    if negotiation is None:
        if alert.requested_by is None:
            raise ValueError("This demand has no owner to negotiate with")
        negotiation = Negotiation(
            negotiation_number=await _next_negotiation_number(pg_db, "BID"),
            builder_id=alert.requested_by,
            supplier_id=vendor.id,
            product_name=alert.item_description,
            quantity=alert.quantity_needed or 1,
            unit=alert.unit or "piece",
            requested_discount=0,
            value=0,
            status="pending",
            demand_alert_id=alert.id,
        )
        pg_db.add(negotiation)
        await pg_db.flush()
        await _post_bid_message(
            pg_db, negotiation, side="builder",
            note=_seed_message(
                {"description": alert.item_description,
                 "quantity": float(alert.quantity_needed or 0),
                 "unit": alert.unit or ""}
            ),
        )

    product = None
    product_uuid = _coerce_uuid(product_id)
    if product_uuid is not None:
        product = (
            await pg_db.execute(select(Product).where(Product.id == product_uuid))
        ).scalars().first()
        if product is None:
            raise ValueError("Product not found")
        if product.vendor_id != vendor.id:
            raise ValueError("That product belongs to another vendor")

    snapshot = None
    if product is not None:
        snapshot = product_snapshot(product, await _primary_image_url(pg_db, product.id))
        negotiation.product_id = product.id
        negotiation.unit = product.unit_of_measure or negotiation.unit

    negotiation.bid_price = bid_price
    negotiation.status = "counter_offered"
    await _post_bid_message(
        pg_db, negotiation, side="supplier",
        note=note or f"Bid: {bid_price:,.2f} per {alert.unit or 'unit'}",
        # The figure is stored on the message as well as the thread so an accept
        # always has a concrete price to read, whichever path created the bid.
        final_price=bid_price,
        product_id=product.id if product is not None else None,
        product_snapshot=snapshot,
    )
    await pg_db.commit()
    await pg_db.refresh(negotiation)
    return negotiation


async def latest_priced_offer(
    pg_db: AsyncSession, negotiation_id: uuid.UUID
) -> Optional[NegotiationCounterOffer]:
    """Newest thread entry that actually carries a price (chat notes excluded)."""
    return (
        await pg_db.execute(
            select(NegotiationCounterOffer)
            .where(
                NegotiationCounterOffer.negotiation_id == negotiation_id,
                NegotiationCounterOffer.final_price.isnot(None),
            )
            .order_by(NegotiationCounterOffer.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


async def mark_demand_agreed(
    pg_db: AsyncSession,
    alert: DemandAlert,
    negotiation: Negotiation,
    *,
    price: float,
    accepted_by: str = "builder",
) -> list[Negotiation]:
    """Close a broadcast on an agreement: one vendor wins, siblings are rejected.

    An alert no longer expires on a clock - reaching this function is what ends
    it. Returns the sibling threads that were rejected so the caller can notify
    those vendors. The caller owns the commit.
    """
    alert.status = "agreed"
    alert.agreed_vendor_id = negotiation.supplier_id
    alert.agreed_price = price
    alert.agreed_at = datetime.utcnow()
    alert.agreed_by = accepted_by

    negotiation.agreed_price = price
    negotiation.agreed_at = datetime.utcnow()
    negotiation.agreed_by = accepted_by
    negotiation.status = "price_agreed"
    negotiation.bid_price = price

    siblings = (
        await pg_db.execute(
            select(Negotiation).where(
                Negotiation.demand_alert_id == alert.id,
                Negotiation.id != negotiation.id,
                Negotiation.status.notin_(("price_agreed", "rejected", "closed")),
            )
        )
    ).scalars().all()
    for sibling in siblings:
        sibling.status = "rejected"
    await pg_db.flush()
    return list(siblings)


def bid_thread_is_closed(negotiation: Negotiation) -> bool:
    """True when this thread can no longer be bid on or accepted."""
    return negotiation.status in ("price_agreed", "rejected", "closed") or (
        getattr(negotiation, "agreed_order_id", None) is not None
    )


def _bid_thread_dict(negotiation: Negotiation) -> Dict[str, Any]:
    vendor = negotiation.supplier
    priced = [c for c in negotiation.counter_offers if c.final_price is not None]
    latest = priced[-1] if priced else None
    return {
        "id": str(negotiation.id),
        "negotiation_number": negotiation.negotiation_number,
        "supplier_id": str(negotiation.supplier_id),
        "supplier": vendor.business_name if vendor else None,
        "bid_price": float(negotiation.bid_price) if negotiation.bid_price is not None else None,
        # Newest priced message - the figure an accept would apply.
        "latest_price": float(latest.final_price) if latest is not None else None,
        "latest_offer": _offer_dict(latest) if latest is not None else None,
        "agreed_price": float(negotiation.agreed_price) if negotiation.agreed_price is not None else None,
        "agreed_order_id": str(negotiation.agreed_order_id) if negotiation.agreed_order_id else None,
        "unit": negotiation.unit,
        "quantity": float(negotiation.quantity or 0),
        "status": negotiation.status,
        "created_at": negotiation.created_at.isoformat() if negotiation.created_at else None,
        "messages": [_offer_dict(c) for c in negotiation.counter_offers],
    }


def _offer_dict(offer: NegotiationCounterOffer) -> Dict[str, Any]:
    """One thread message, including any catalogue product the vendor attached."""
    return {
        "id": str(offer.id),
        "offered_by": offer.offered_by,
        "final_price": float(offer.final_price) if offer.final_price is not None else None,
        "note": offer.note,
        "product_id": str(offer.product_id) if offer.product_id else None,
        "product": offer.product_snapshot,
        "created_at": offer.created_at.isoformat() if offer.created_at else None,
    }


async def list_alert_bids(pg_db: AsyncSession, alert_id: uuid.UUID) -> List[Dict[str, Any]]:
    """Every vendor bid thread opened against a demand alert."""
    rows = (
        await pg_db.execute(
            select(Negotiation)
            .options(joinedload(Negotiation.supplier), selectinload(Negotiation.counter_offers))
            .where(Negotiation.demand_alert_id == alert_id)
            .order_by(Negotiation.created_at.asc())
        )
    ).scalars().all()
    return [_bid_thread_dict(n) for n in rows]


async def vendor_sells_matching_product(
    pg_db: AsyncSession, vendor_id: uuid.UUID, description: str
) -> bool:
    """True when the vendor has an active product matching the line keywords."""
    matched = await _match_vendors(pg_db, description, limit=9999)
    return any(v.id == vendor_id for v in matched)
