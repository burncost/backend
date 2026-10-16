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


def normalise_line(line: Dict[str, Any], default_city: str = "Abuja") -> Dict[str, Any]:
    """Flatten the several line shapes BOQ generation/verification emit into one."""
    quantity = line.get("quantity") or line.get("qty") or 0
    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        quantity = 0.0
    return {
        "description": (line.get("description") or line.get("item_description") or "").strip(),
        "quantity": quantity,
        "unit": (line.get("unit") or "").strip(),
        "city": (line.get("city") or default_city or "Abuja").strip() or "Abuja",
    }


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
    )
    pg_db.add(alert)
    await pg_db.flush()
    return alert


async def _match_vendors(
    pg_db: AsyncSession, description: str, limit: int = _MAX_VENDORS_PER_LINE
) -> Sequence[Vendor]:
    """Vendors with an active product whose text matches the line's keywords."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", (description or "").lower()) if len(t) > 3]
    if not tokens:
        return []
    token = max(tokens, key=len)
    pattern = f"%{token}%"
    stmt = (
        select(Vendor)
        .join(Product, Product.vendor_id == Vendor.id)
        .where(
            Product.status == ProductStatus.ACTIVE,
            func.coalesce(Vendor.verification_status, "pending")
            .notin_(_EXCLUDED_VENDOR_STATUS),
            or_(
                func.lower(Product.name).like(pattern),
                func.lower(func.coalesce(Product.short_description, "")).like(pattern),
                func.lower(func.coalesce(Product.description, "")).like(pattern),
                func.lower(func.coalesce(Product.meta_keywords, "")).like(pattern),
            ),
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
) -> NegotiationCounterOffer:
    """Append a message to a bid thread (reuses the counter-offer table)."""
    offer = NegotiationCounterOffer(
        negotiation_id=negotiation.id,
        offered_by=side,
        discount_percent=discount_percent,
        note=note,
    )
    pg_db.add(offer)
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
    summary = {"alerts_created": 0, "bids_opened": 0, "notified": 0}
    if pg_db is None:
        return summary

    requester = _coerce_uuid(requested_by)
    normalised = [normalise_line(line, city) for line in (lines or [])]
    normalised = [line for line in normalised if line["description"]][:_MAX_BID_LINES]
    if not normalised:
        return summary

    for line in normalised:
        try:
            alert = await _create_alert(pg_db, line, project_title, requester)
            if alert is None:
                continue
            summary["alerts_created"] += 1
            # Threads need an owner (builder_id is NOT NULL) - skip for guests.
            if requester is None or not match_vendors:
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
) -> Negotiation:
    """Vendor places/updates a bid on a demand alert. Opens the thread if needed."""
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

    negotiation.bid_price = bid_price
    negotiation.status = "counter_offered"
    await _post_bid_message(
        pg_db, negotiation, side="supplier",
        note=note or f"Bid: {bid_price:,.2f} per {alert.unit or 'unit'}",
    )
    await pg_db.commit()
    await pg_db.refresh(negotiation)
    return negotiation


def _bid_thread_dict(negotiation: Negotiation) -> Dict[str, Any]:
    vendor = negotiation.supplier
    return {
        "id": str(negotiation.id),
        "negotiation_number": negotiation.negotiation_number,
        "supplier_id": str(negotiation.supplier_id),
        "supplier": vendor.business_name if vendor else None,
        "bid_price": float(negotiation.bid_price) if negotiation.bid_price is not None else None,
        "unit": negotiation.unit,
        "quantity": float(negotiation.quantity or 0),
        "status": negotiation.status,
        "created_at": negotiation.created_at.isoformat() if negotiation.created_at else None,
        "messages": [
            {
                "id": str(c.id),
                "offered_by": c.offered_by,
                "note": c.note,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in negotiation.counter_offers
        ],
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
