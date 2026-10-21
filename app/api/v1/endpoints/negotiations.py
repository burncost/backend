"""User-facing negotiation endpoints (Phase 2)."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.vendor import Vendor
from app.models.product import Product
from app.models.negotiation import Negotiation, NegotiationCounterOffer, NegotiationAuditEntry
from app.models.notification import Notification
from app.services import demand_broadcast_service as demand_broadcast

router = APIRouter()
logger = logging.getLogger(__name__)

# Console/staff roles that receive the private-agreement notification. Kept in
# sync with ADMIN_INBOX_ROLES (admin_phase1.py) so the row is visible in the
# admin inbox + unread badge and is never counted as a high-priority "alert".
CONSOLE_NOTIFICATION_ROLES = ("admin", "super_admin", "manager", "support", "marketing")


async def _notify_admins_of_private_agreement(db: AsyncSession, n: Negotiation, accepted_by: str) -> None:
    """One persistent notification per console user when a buyer & vendor agree a price.

    Buyer and vendor settle this deal between themselves, so this is the only way
    the Negotiation Center learns the agreed figure. Best-effort only: the
    agreement is already committed and a failure here must never turn a
    successful accept into a 500.
    """
    try:
        supplier_name = (await db.execute(
            select(Vendor.business_name).where(Vendor.id == n.supplier_id)
        )).scalar_one_or_none() or "the supplier"
        buyer = (await db.execute(
            select(User.email).where(User.id == n.builder_id)
        )).scalar_one_or_none() or "a buyer"
        agreed = float(n.agreed_price or 0)
        staff_ids = (await db.execute(
            select(User.id).where(User.role.in_(CONSOLE_NOTIFICATION_ROLES))
        )).scalars().all()
        for uid in staff_ids:
            db.add(Notification(
                user_id=uid,
                type="negotiation",
                title="Buyer & vendor agreed a price privately",
                message=(f"{n.negotiation_number}: {buyer} and {supplier_name} agreed "
                         f"NGN {agreed:,.2f}/unit for {n.product_name} "
                         f"(accepted by {accepted_by}). Review the negotiation."),
                read=False,
                link="/negotiation",
            ))
        if staff_ids:
            await db.commit()
    except Exception as e:  # pragma: no cover - never block a completed agreement
        logger.error(f"Failed to notify admins of agreed price for {n.negotiation_number}: {e}")


async def _notify_counterparty(db: AsyncSession, row: Negotiation, side: str, action: str, payload: dict) -> None:
    """Persistent notification for the party who did NOT perform `action`.

    Complements the vendor row raised in `create_negotiation` so the whole loop
    (request -> counter -> propose -> agree) stays visible on the other side's
    notifications inbox. `row` is the re-fetched negotiation (supplier already
    eager-loaded - never lazy-load in async). Best-effort only: the action above
    is already committed and a failure here must never turn it into a 500.
    """
    try:
        if side == "builder":
            # Counterparty of the builder is the vendor's user account.
            target_id = getattr(getattr(row, "supplier", None), "user_id", None)
            link = "/supplier-dashboard"
        else:
            target_id = row.builder_id
            link = "/dashboard/negotiations"
        if not target_id:
            return
        actor = "Vendor" if side == "supplier" else "Builder"
        if action == "propose_price":
            title = "Final price proposed"
            message = (f"{actor} proposed NGN {float(payload.get('final_price') or 0):,.2f}/unit for "
                       f"{row.product_name} ({row.negotiation_number}). Accept or counter.")
        elif action == "accept_price":
            title = "Final price agreed"
            message = (f"{actor} accepted NGN {float(row.agreed_price or 0):,.2f}/unit for "
                       f"{row.product_name} ({row.negotiation_number}). "
                       f"It is applied automatically at checkout.")
        elif action in ("decline", "reject"):
            title = "Negotiation declined"
            message = f"{actor} declined {row.negotiation_number} for {row.product_name}."
        elif action == "accept":
            title = "Negotiation accepted"
            message = f"{actor} accepted {row.negotiation_number} for {row.product_name}."
        else:
            title = "New counter-offer"
            message = (f"{actor} countered with {payload.get('discount_percent')}% on "
                       f"{row.product_name} ({row.negotiation_number}).")
        db.add(Notification(
            user_id=target_id,
            type="negotiation",
            title=title,
            message=message,
            read=False,
            link=link,
        ))
        await db.commit()
    except Exception as e:  # pragma: no cover - never block a completed action
        logger.error(f"Failed to notify counterparty of {action} for {row.negotiation_number}: {e}")


def _implied_discount(base_price, price) -> float | None:
    """The % off the catalogue list price that `price` represents.

    A naira figure and a percentage are two views of one deal, so the percentage
    is always recomputed from the list price instead of being trusted from
    storage: the row can then never show `₦11,200 (8.33% off)` because the buyer
    once asked for 8.33%.
    """
    try:
        base = float(base_price)
        value = float(price)
    except (TypeError, ValueError):
        return None
    if base <= 0 or value < 0:
        return None
    return round((base - value) / base * 100, 2)


def _final_discount_pct(n: Negotiation, base_price=None) -> float | None:
    """The agreed discount % for a list row - never NULL once the deal is settled.

    A stored `final_discount` (admin approve / legacy `accept`) always wins.
    Otherwise the figure is derived: `price_agreed` rows recompute the % off the
    catalogue list price, falling back to `counter_offer` (which `accept_price`
    already stores as the implied %); any other row uses `counter_offer`.
    """
    if n.final_discount is not None:
        return float(n.final_discount)
    if n.status == "price_agreed":
        pct = _implied_discount(base_price, n.agreed_price)
        if pct is not None:
            return pct
    return float(n.counter_offer) if n.counter_offer is not None else None


async def _base_price(db: AsyncSession, product_id) -> float | None:
    """Catalogue list price of a product, or None when it is not in the catalogue."""
    if not product_id:
        return None
    base = (await db.execute(
        select(Product.base_price).where(Product.id == product_id)
    )).scalar_one_or_none()
    return float(base) if base is not None else None


async def _base_prices(db: AsyncSession, product_ids) -> dict:
    """List prices for many products at once, keyed by `str(product.id)`.

    One query for a whole negotiation list; an empty list of ids costs nothing.
    """
    ids = [pid for pid in product_ids if pid]
    if not ids:
        return {}
    rows = (await db.execute(
        select(Product.id, Product.base_price).where(Product.id.in_(ids))
    )).all()
    return {str(pid): float(bp) for pid, bp in rows if bp is not None}


def _offer_dict(c: NegotiationCounterOffer, base_price=None) -> dict:
    """One counter-offer row: a priced proposal carries the % its naira implies.

    The stored `discount_percent` is only a fallback (percentage-only counters and
    rows written before the figure was derived).
    """
    pct = _implied_discount(base_price, c.final_price) if c.final_price is not None else None
    return {
        "id": str(c.id),
        "offered_by": c.offered_by,
        "discount_percent": pct if pct is not None else float(c.discount_percent or 0),
        "final_price": float(c.final_price) if c.final_price is not None else None,
        "note": c.note,
        # Catalogue product attached to this message (vendor product picker) and
        # the snapshot taken at offer time, so the buyer sees exactly what was
        # offered even after the vendor edits their catalogue.
        "product_id": str(c.product_id) if getattr(c, "product_id", None) else None,
        "product": getattr(c, "product_snapshot", None),
        "created_at": c.created_at.isoformat() if getattr(c, "created_at", None) else None,
    }


def _neg_dict(n: Negotiation, base_price=None) -> dict:
    return {
        "id": str(n.id),
        "negotiation_number": n.negotiation_number,
        "supplier_id": str(n.supplier_id),
        # Product id (nullable) so buyer pages can match a deal to a product.
        "product_id": str(n.product_id) if n.product_id else None,
        "supplier": n.supplier.business_name if n.supplier else None,
        "product": n.product_name,
        "category": n.category,
        "quantity": float(n.quantity or 0),
        "unit": n.unit,
        "requested_discount": float(n.requested_discount or 0),
        # The buyer's opening absolute per-unit bid (NULL for percentage-based
        # requests, which is how every pre-existing row reads back).
        "requested_price": float(n.requested_price) if n.requested_price is not None else None,
        "counter_offer": float(n.counter_offer) if n.counter_offer is not None else None,
        # Never NULL once the deal is settled: derived from `agreed_price` for a
        # `price_agreed` row (see `_final_discount_pct`).
        "final_discount": _final_discount_pct(n, base_price),
        "value": float(n.value or 0),
        "status": n.status,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        # Two-party agreed final price (absolute per-unit). NULL until agreed.
        "agreed_price": float(n.agreed_price) if n.agreed_price is not None else None,
        "agreed_at": n.agreed_at.isoformat() if n.agreed_at else None,
        "agreed_by": n.agreed_by,
        # Set once the agreement priced an order: the price is spent and the
        # buyer can no longer seed a cart line from it.
        "agreed_order_id": str(n.agreed_order_id) if getattr(n, "agreed_order_id", None) else None,
        "demand_alert_id": str(n.demand_alert_id) if getattr(n, "demand_alert_id", None) else None,
        "counter_offers": [_offer_dict(c, base_price) for c in n.counter_offers],
    }


@router.post("/")
async def create_negotiation(payload: dict, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Builder opens a price request against a supplier.

    Buyers bid an absolute per-unit price (`requested_price`): the discount
    percentage is derived here from the product's catalogue `base_price`, so the
    buyer never has to do the maths. A lone `requested_discount` keeps working
    unchanged for any legacy caller.
    """
    supplier_id = payload.get("supplier_id")
    product_name = payload.get("product_name")
    requested_discount = payload.get("requested_discount")
    requested_price = payload.get("requested_price")
    if not supplier_id or not product_name or (requested_discount is None and requested_price is None):
        raise HTTPException(400, "supplier_id, product_name and requested_price (or requested_discount) required")
    supplier = (await db.execute(select(Vendor).where(Vendor.id == UUID(supplier_id)))).scalar_one_or_none()
    if not supplier:
        raise HTTPException(404, "Supplier not found")
    # Absolute-bid path: validate the figure against the catalogue list price and
    # derive the percentage the supplier/reporting layers expect.
    base_price = None
    bid = None
    if requested_price is not None:
        product_id = payload.get("product_id")
        if not product_id:
            raise HTTPException(400, "product_id required to bid an absolute price")
        base_price = (await db.execute(
            select(Product.base_price).where(Product.id == UUID(product_id))
        )).scalar_one_or_none()
        try:
            bid = float(requested_price)
        except (TypeError, ValueError):
            raise HTTPException(400, "requested_price must be a number")
        if base_price is None:
            raise HTTPException(400, "This product is not in the catalogue, so that price cannot be checked")
        base_price = float(base_price)
        if bid <= 0:
            raise HTTPException(400, "Your price must be greater than 0")
        if base_price > 0 and bid >= base_price:
            raise HTTPException(400, f"Your price must be below the supplier's ₦{base_price:,.2f}")
        requested_discount = round((base_price - bid) / base_price * 100, 2) if base_price > 0 else 0
    quantity = payload.get("quantity", 1)
    value = payload.get("value")
    if bid is not None and not value:
        # Server-side maths: the line value of the bid when the caller sent none.
        try:
            value = round(bid * float(quantity or 1), 2)
        except (TypeError, ValueError):
            value = 0
    count = (await db.execute(select(func.count(Negotiation.id)))).scalar() or 0
    negotiation = Negotiation(
        negotiation_number=f"NEG-{datetime.utcnow().strftime('%Y%m%d')}-{count + 1:05d}",
        builder_id=current_user.id,
        supplier_id=supplier.id,
        product_id=UUID(payload["product_id"]) if payload.get("product_id") else None,
        product_name=product_name,
        category=payload.get("category"),
        quantity=quantity,
        unit=payload.get("unit", "piece"),
        requested_discount=requested_discount,
        requested_price=bid,
        value=value if value is not None else 0,
        status="pending",
    )
    db.add(negotiation)
    # Let the supplier know a builder has opened a negotiation request. Persistent
    # in-app row (type "negotiation") surfaced on the supplier dashboard.
    try:
        req_pct = float(requested_discount)
        discount_txt = f"{req_pct:g}%"
    except (TypeError, ValueError):
        discount_txt = f"{requested_discount}%"
    if bid is not None:
        list_txt = f"{base_price:,.2f}" if base_price is not None else "list"
        message = (f"{current_user.email} bid NGN {bid:,.2f}/unit ({discount_txt} below your "
                   f"NGN {list_txt} list price) on {product_name} "
                   f"({negotiation.negotiation_number}).")
    else:
        message = (f"{current_user.email} requested a {discount_txt} discount on "
                   f"{product_name} ({negotiation.negotiation_number}).")
    db.add(Notification(
        user_id=supplier.user_id,
        type="negotiation",
        title="New negotiation request",
        message=message,
        read=False,
        link="/supplier-dashboard",
    ))
    await db.commit()
    await db.refresh(negotiation)
    # Eager-load the relations `_neg_dict` reads (never lazy-load in async).
    row = (await db.execute(
        select(Negotiation)
        .options(selectinload(Negotiation.supplier), selectinload(Negotiation.counter_offers))
        .where(Negotiation.id == negotiation.id)
    )).scalar_one()
    return _neg_dict(row, base_price)


@router.get("/")
async def list_my_negotiations(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List the current user's (builder or supplier) negotiations."""
    vendor = (await db.execute(select(Vendor).where(Vendor.user_id == current_user.id))).scalar_one_or_none()
    # Eager-load what `_neg_dict` reads (never lazy-load in async).
    eager = (selectinload(Negotiation.supplier), selectinload(Negotiation.counter_offers))
    if vendor:
        rows = (await db.execute(
            select(Negotiation).options(*eager).where(Negotiation.supplier_id == vendor.id)
        )).scalars().all()
    else:
        rows = (await db.execute(
            select(Negotiation).options(*eager).where(Negotiation.builder_id == current_user.id)
        )).scalars().all()
    # One list price lookup for the whole page: each row is rendered with the %
    # its figures imply (`_offer_dict`), not with a stored percentage.
    base_prices = await _base_prices(db, [n.product_id for n in rows])
    return {"negotiations": [
        _neg_dict(n, base_prices.get(str(n.product_id))) for n in rows
    ]}


@router.post("/{negotiation_id}/counter")
async def send_counter_offer(negotiation_id: UUID, payload: dict, current_user=Depends(get_current_user), db=Depends(get_db)):
    """Builder or supplier sends a counter-offer (or accepts/declines)."""
    from sqlalchemy import select as sa_select
    n = (await db.execute(sa_select(Negotiation).where(Negotiation.id == negotiation_id))).scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Negotiation not found")
    vendor = (await db.execute(sa_select(Vendor).where(Vendor.user_id == current_user.id))).scalar_one_or_none()
    if vendor and vendor.id == n.supplier_id:
        side = "supplier"
    elif current_user.id == n.builder_id:
        side = "builder"
    else:
        raise HTTPException(403, "Not a party to this negotiation")
    from app.models.negotiation import NegotiationCounterOffer
    action = payload.get("action", "counter")
    dp = payload.get("discount_percent")
    fp = payload.get("final_price")
    note = payload.get("note", "")
    # Catalogue list price when this negotiation is tied to a product: every naira
    # figure below is measured as the % it represents off that price, so the two
    # can never disagree (and a stale stored % cannot leak into the row).
    base_price = await _base_price(db, n.product_id)

    # Once both parties agree a final price the negotiation is locked: no further
    # counter-proposals from either side (prevents re-opening an agreed deal).
    if n.status == "price_agreed":
        raise HTTPException(400, "Final price already agreed")
    # ...and a spent agreement (already priced an order) is final forever.
    if getattr(n, "agreed_order_id", None) is not None:
        raise HTTPException(400, "This agreement has already been checked out")

    if action == "propose_price":
        # Propose an absolute per-unit final price for the counterparty to accept.
        try:
            fp_val = float(fp)
        except (TypeError, ValueError):
            raise HTTPException(400, "final_price required for propose_price")
        if fp_val <= 0:
            raise HTTPException(400, "final_price must be greater than 0")
        # Store the percentage the naira figure actually implies (same derivation as
        # a buyer's bid), so the row never shows a stale % beside the proposed price.
        if dp is None:
            implied = _implied_discount(base_price, fp_val)
            if implied is not None:
                dp = implied
        n.status = "price_proposed"
        attached_product = payload.get("product_id") if side == "supplier" else None
        if attached_product:
            # The vendor quoted a catalogue product: one row carries the price AND
            # the product snapshot, so the buyer can accept and buy it directly.
            try:
                await demand_broadcast.attach_product_to_negotiation(
                    db, n,
                    vendor_id=vendor.id,
                    product_id=UUID(str(attached_product)),
                    price=fp_val,
                    note=note,
                    side=side,
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            await db.flush()
        else:
            # No product_id means "keep whatever was attached before": a plain
            # price counter must never silently clear the product on the deal.
            db.add(NegotiationCounterOffer(
                negotiation_id=n.id, offered_by=side,
                discount_percent=dp if dp is not None else n.requested_discount,
                final_price=fp_val, note=note,
            ))
    elif action == "accept_price":
        # Only the counterparty of the last proposer may accept (strictly the two
        # parties of this negotiation - the guard above already rejected others).
        # The newest *priced* entry is the live offer; chat notes are ignored.
        last = await demand_broadcast.latest_priced_offer(db, n.id)
        if last is None or last.final_price is None:
            raise HTTPException(400, "No final price has been proposed yet")
        if last.offered_by == side:
            raise HTTPException(400, "The proposing party cannot accept its own price")
        n.agreed_price = last.final_price
        n.agreed_at = datetime.utcnow()
        n.agreed_by = side
        n.status = "price_agreed"
        # Keyframe: the proposal may have attached a catalogue product; a plain
        # counter keeps the existing attachment rather than clearing it.
        if getattr(last, "product_id", None) is not None:
            n.product_id = last.product_id
        # A bid thread belongs to a demand broadcast: agreeing here also closes
        # the broadcast and rejects the competing vendors, so a demand can never
        # be "agreed" in one thread and still open in another.
        if getattr(n, "demand_alert_id", None) is not None:
            from app.models.demand_alert import DemandAlert
            alert = (await db.execute(
                sa_select(DemandAlert).where(DemandAlert.id == n.demand_alert_id)
            )).scalar_one_or_none()
            if alert is not None and (alert.status or "") != "agreed":
                await demand_broadcast.mark_demand_agreed(
                    db, alert, n, price=float(last.final_price), accepted_by=side
                )
        # `counter_offer` is a percentage column (see `_neg_dict` + the console
        # analytics): keep the agreed naira in `agreed_price` only, otherwise the
        # row renders "11000%". The % is the one the agreed naira implies.
        agreed_pct = _implied_discount(base_price, last.final_price)
        n.counter_offer = (agreed_pct if agreed_pct is not None else
                           (float(last.discount_percent) if last.discount_percent is not None
                            else float(n.requested_discount or 0)))
        # Console-visible audit trail: the two parties settled privately, so this
        # entry is how the Negotiation Center learns the agreed figure.
        db.add(NegotiationAuditEntry(
            negotiation_id=n.id,
            action="Final price agreed (buyer & vendor)",
            performed_by=side,
            new_value=str(n.agreed_price),
            note=note or None,
        ))
    elif action in ("accept",):
        n.status = "approved"
        n.final_discount = dp or n.requested_discount
        n.counter_offer = n.final_discount
    elif action in ("decline", "reject"):
        n.status = "rejected"
    else:
        if dp is None:
            raise HTTPException(400, "discount_percent required for counter")
        n.status = "counter_offered"
        n.counter_offer = dp
    if action not in ("propose_price", "accept_price"):
        db.add(NegotiationCounterOffer(
            negotiation_id=n.id, offered_by=side,
            discount_percent=dp if dp is not None else n.requested_discount,
            note=note,
        ))
    await db.commit()
    # Eager-load the relations `_neg_dict` reads (never lazy-load in async).
    row = (await db.execute(
        sa_select(Negotiation)
        .options(selectinload(Negotiation.supplier), selectinload(Negotiation.counter_offers))
        .where(Negotiation.id == n.id)
    )).scalar_one()
    if action == "accept_price":
        # Buyer & vendor settled a price between themselves: tell the console.
        await _notify_admins_of_private_agreement(db, row, side)
    # ...and tell the party who did not act, so both sides see the loop progress.
    await _notify_counterparty(db, row, side, action, payload)
    return _neg_dict(row, base_price)


@router.post("/{negotiation_id}/attach-product")
async def attach_product(
    negotiation_id: UUID,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vendor attaches one of their catalogue products to an existing negotiation.

    Sending `price` in the same call also proposes that per-unit figure, so the
    buyer can accept the product and its price together. Without a price the
    attachment is informational and the thread's price negotiation continues.
    """
    n = (await db.execute(
        select(Negotiation).where(Negotiation.id == negotiation_id)
    )).scalars().first()
    if n is None:
        raise HTTPException(404, "Negotiation not found")
    vendor = (await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )).scalars().first()
    if vendor is None or vendor.id != n.supplier_id:
        raise HTTPException(403, "Only the supplier in this negotiation can attach a product")
    if n.status == "price_agreed" or n.agreed_order_id is not None:
        raise HTTPException(400, "This negotiation is already settled")

    try:
        product_id = UUID(str(payload.get("product_id")))
    except (TypeError, ValueError):
        raise HTTPException(400, "A valid product_id is required")

    try:
        offer = await demand_broadcast.attach_product_to_negotiation(
            db, n,
            vendor_id=vendor.id,
            product_id=product_id,
            price=payload.get("price"),
            note=payload.get("note", ""),
            side="supplier",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if payload.get("price") is not None:
        n.status = "price_proposed"

    await db.commit()
    row = (await db.execute(
        select(Negotiation)
        .options(selectinload(Negotiation.supplier), selectinload(Negotiation.counter_offers))
        .where(Negotiation.id == n.id)
    )).scalar_one()
    return {
        "offer_id": str(offer.id),
        "negotiation": _neg_dict(row, await _base_price(db, n.product_id)),
    }
