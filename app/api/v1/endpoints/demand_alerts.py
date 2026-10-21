"""User-facing demand alert endpoints (Phase 4)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from datetime import datetime
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.vendor import Vendor
from app.models.negotiation import Negotiation, NegotiationAuditEntry
from app.models.notification import Notification
from app.models.product import Product
from app.models.demand_alert import DemandAlert, demand_alert_cutoff
from app.services import demand_broadcast_service as demand_broadcast

router = APIRouter()


def _alert_dict(alert: DemandAlert) -> dict:
    return {
        "id": str(alert.id),
        "item_description": alert.item_description,
        "city": alert.city,
        "quantity_needed": float(alert.quantity_needed) if alert.quantity_needed is not None else None,
        "unit": alert.unit,
        "project_title": alert.project_title,
        "status": alert.status,
        "material_key": alert.material_key,
        "spec": alert.spec,
        "source_ref": alert.source_ref,
        # Present only once the broadcast ended in an agreement.
        "agreed_vendor_id": str(alert.agreed_vendor_id) if alert.agreed_vendor_id else None,
        "agreed_price": float(alert.agreed_price) if alert.agreed_price is not None else None,
        "agreed_at": alert.agreed_at.isoformat() if alert.agreed_at else None,
        "agreed_by": alert.agreed_by,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }


@router.post("/")
async def create_demand_alert(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Builder posts an item requirement (demand alert)."""
    item_description = payload.get("item_description")
    city = payload.get("city")
    if not item_description or not city:
        raise HTTPException(400, "item_description and city required")
    # Same (city, material) identity the BOQ broadcast uses, so a hand-raised
    # alert and a generated one land on the same dedupe key.
    dedupe_key = demand_broadcast._dedupe_key(
        {"description": item_description, "material_key": payload.get("material_key")},
        city,
    )
    alert = DemandAlert(
        item_description=item_description,
        city=city,
        quantity_needed=payload.get("quantity_needed"),
        unit=payload.get("unit"),
        project_title=payload.get("project_title"),
        requested_by=current_user.id,
        status="pending",
        material_key=payload.get("material_key"),
        spec=payload.get("spec"),
        source_ref=payload.get("source_ref"),
        dedupe_key=dedupe_key,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return {"id": str(alert.id), "status": alert.status, "city": alert.city}


@router.get("/")
async def list_demand_alerts(
    city: str = None,
    db: AsyncSession = Depends(get_db),
):
    """List open demand alerts (optionally filtered by city).

    An alert now stays open until a buyer and a vendor agree a price (or an admin
    closes it). The 48h cutoff is retained only at its frozen release value, so
    alerts raised under the old rule keep their historical expiry while nothing
    raised since can expire.
    """
    query = (
        select(DemandAlert)
        .where(DemandAlert.created_at >= demand_alert_cutoff())
        .where(or_(DemandAlert.status.is_(None), DemandAlert.status.notin_(("closed", "agreed"))))
        .order_by(DemandAlert.created_at.desc())
    )
    if city:
        # Case/whitespace-insensitive so "Abuja" matches a vendor's stored "abuja ".
        query = query.where(func.lower(func.trim(DemandAlert.city)) == func.lower(city.strip()))
    rows = (await db.execute(query)).scalars().all()
    return {"alerts": [_alert_dict(a) for a in rows]}


@router.get("/{alert_id}/bids")
async def list_demand_alert_bids(
    alert_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Per-vendor bid threads opened against a demand alert (public read)."""
    alert = (await db.execute(
        select(DemandAlert).where(DemandAlert.id == alert_id)
    )).scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Demand alert not found")
    bids = await demand_broadcast.list_alert_bids(db, alert_id)
    return {
        "alert_id": str(alert_id),
        "item_description": alert.item_description,
        "bids": bids,
    }


@router.post("/{alert_id}/bid")
async def place_demand_alert_bid(
    alert_id: UUID,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vendor places a bid on a demand alert.

    Guarded so only a vendor that actually sells a matching product can bid -
    otherwise the demand feed becomes a cold-call channel.
    """
    vendor = (await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )).scalar_one_or_none()
    if not vendor:
        raise HTTPException(404, "Vendor profile not found")

    alert = (await db.execute(
        select(DemandAlert).where(DemandAlert.id == alert_id)
    )).scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Demand alert not found")
    if (alert.status or "") in ("closed", "agreed"):
        raise HTTPException(400, "This demand request has been closed")

    bid_price = payload.get("bid_price")
    try:
        bid_price = float(bid_price)
    except (TypeError, ValueError):
        raise HTTPException(400, "A numeric bid_price is required")
    if bid_price <= 0:
        raise HTTPException(400, "bid_price must be greater than zero")

    if not await demand_broadcast.vendor_sells_matching_product(
        db, vendor.id, alert.item_description
    ):
        raise HTTPException(403, "You do not sell a matching product for this demand")

    try:
        negotiation = await demand_broadcast.place_vendor_bid(
            db, alert, vendor,
            bid_price=bid_price,
            note=payload.get("note", ""),
            product_id=payload.get("product_id"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    return {
        "negotiation_id": str(negotiation.id),
        "negotiation_number": negotiation.negotiation_number,
        "bid_price": float(negotiation.bid_price or 0),
        "status": negotiation.status,
    }


@router.post("/{alert_id}/attach-product")
async def attach_product_to_bid(
    alert_id: UUID,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vendor attaches one of their catalogue products to their bid on an alert.

    Everything needed to buy arrives in one call: product id, buy price and an
    immutable snapshot. Calling it again is safe - the later attachment simply
    becomes the newest priced offer.
    """
    vendor = (await db.execute(
        select(Vendor).where(Vendor.user_id == current_user.id)
    )).scalar_one_or_none()
    if not vendor:
        raise HTTPException(404, "Vendor profile not found")

    alert = (await db.execute(
        select(DemandAlert).where(DemandAlert.id == alert_id)
    )).scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Demand alert not found")
    if (alert.status or "") in ("closed", "agreed"):
        raise HTTPException(400, "This demand request has been closed")

    try:
        product_id = UUID(str(payload.get("product_id")))
    except (TypeError, ValueError):
        raise HTTPException(400, "A valid product_id is required")

    negotiation = (await db.execute(
        select(Negotiation).where(
            Negotiation.demand_alert_id == alert_id,
            Negotiation.supplier_id == vendor.id,
        )
    )).scalars().first()
    if negotiation is None:
        # Attaching a product is itself a bid: open the vendor's thread first.
        # The quote defaults to the vendor's own list price - never a placeholder,
        # which the buyer could otherwise accept by mistake.
        product = (await db.execute(
            select(Product).where(Product.id == product_id)
        )).scalars().first()
        if product is None or product.vendor_id != vendor.id:
            raise HTTPException(400, "That product is not in your catalogue")
        try:
            quote = float(payload.get("price") or product.base_price or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "price must be a number")
        if quote <= 0:
            raise HTTPException(400, "price must be greater than zero")
        try:
            negotiation = await demand_broadcast.place_vendor_bid(
                db, alert, vendor,
                bid_price=quote,
                note=payload.get("note", ""),
                product_id=product_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {
            "negotiation_id": str(negotiation.id),
            "product_id": str(negotiation.product_id) if negotiation.product_id else None,
            "final_price": quote,
        }

    try:
        offer = await demand_broadcast.attach_product_to_negotiation(
            db, negotiation,
            vendor_id=vendor.id,
            product_id=product_id,
            price=payload.get("price"),
            note=payload.get("note", ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    await db.commit()
    return {
        "negotiation_id": str(negotiation.id),
        "offer_id": str(offer.id),
        "product_id": str(offer.product_id) if offer.product_id else None,
        "final_price": float(offer.final_price) if offer.final_price is not None else None,
    }


@router.post("/{alert_id}/bids/{negotiation_id}/accept")
async def accept_demand_bid(
    alert_id: UUID,
    negotiation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Builder accepts a vendor's bid: the broadcast closes and the price is fixed.

    Only the buyer who raised the demand may accept. The accepted figure is the
    newest priced message in the thread, and every sibling thread is rejected in
    the same transaction so two vendors can never both believe they won.
    """
    alert = (await db.execute(
        select(DemandAlert).where(DemandAlert.id == alert_id)
    )).scalar_one_or_none()
    if not alert:
        raise HTTPException(404, "Demand alert not found")
    if alert.requested_by is None or alert.requested_by != current_user.id:
        raise HTTPException(403, "Only the buyer who raised this demand can accept a bid")
    if (alert.status or "") == "agreed":
        raise HTTPException(400, "This demand has already been agreed")
    if (alert.status or "") == "closed":
        raise HTTPException(400, "This demand request has been closed")

    negotiation = (await db.execute(
        select(Negotiation).where(
            Negotiation.id == negotiation_id,
            Negotiation.demand_alert_id == alert_id,
        )
    )).scalars().first()
    if negotiation is None:
        raise HTTPException(404, "Bid not found on this demand")
    if negotiation.agreed_order_id is not None:
        raise HTTPException(400, "This agreement has already been checked out")

    offer = await demand_broadcast.latest_priced_offer(db, negotiation.id)
    if offer is None or offer.final_price is None:
        raise HTTPException(400, "This vendor has not quoted a price yet")
    price = float(offer.final_price)

    siblings = await demand_broadcast.mark_demand_agreed(
        db, alert, negotiation, price=price, accepted_by="builder"
    )
    db.add(NegotiationAuditEntry(
        negotiation_id=negotiation.id,
        action="Bid accepted by buyer",
        performed_by="builder",
        new_value=str(price),
        note=f"Demand '{alert.item_description}' awarded.",
    ))

    # Tell the winner they can be checked out, and everyone else they lost.
    vendor = (await db.execute(
        select(Vendor).where(Vendor.id == negotiation.supplier_id)
    )).scalar_one_or_none()
    if vendor and vendor.user_id:
        db.add(Notification(
            user_id=vendor.user_id,
            type="demand",
            title="Your bid was accepted",
            message=(f"{current_user.email} accepted your bid of NGN {price:,.2f} "
                     f"per {negotiation.unit or 'unit'} for {alert.item_description}."),
            link="/supplier-dashboard?tab=demand-alerts",
        ))
    for sibling in siblings:
        losing = (await db.execute(
            select(Vendor.user_id).where(Vendor.id == sibling.supplier_id)
        )).scalar_one_or_none()
        if losing:
            db.add(Notification(
                user_id=losing,
                type="demand",
                title="Demand awarded to another vendor",
                message=f"{alert.item_description} has been awarded to another supplier.",
                link="/supplier-dashboard?tab=demand-alerts",
            ))

    await db.commit()
    return {
        "alert_id": str(alert.id),
        "negotiation_id": str(negotiation.id),
        "agreed_price": price,
        "status": alert.status,
        "rejected_bids": len(siblings),
    }