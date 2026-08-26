"""User-facing negotiation endpoints (Phase 2)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from uuid import UUID

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.vendor import Vendor
from app.models.negotiation import Negotiation, NegotiationCounterOffer

router = APIRouter()


def _neg_dict(n: Negotiation) -> dict:
    return {
        "id": str(n.id),
        "negotiation_number": n.negotiation_number,
        "supplier_id": str(n.supplier_id),
        "supplier": n.supplier.business_name if n.supplier else None,
        "product": n.product_name,
        "category": n.category,
        "quantity": float(n.quantity or 0),
        "unit": n.unit,
        "requested_discount": float(n.requested_discount or 0),
        "counter_offer": float(n.counter_offer) if n.counter_offer is not None else None,
        "final_discount": float(n.final_discount) if n.final_discount is not None else None,
        "value": float(n.value or 0),
        "status": n.status,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "counter_offers": [
            {"id": str(c.id), "offered_by": c.offered_by,
             "discount_percent": float(c.discount_percent or 0), "note": c.note}
            for c in n.counter_offers
        ],
    }


@router.post("/")
async def create_negotiation(payload: dict, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Builder creates a discount request against a supplier."""
    supplier_id = payload.get("supplier_id")
    product_name = payload.get("product_name")
    requested_discount = payload.get("requested_discount")
    if not supplier_id or not product_name or requested_discount is None:
        raise HTTPException(400, "supplier_id, product_name, requested_discount required")
    supplier = (await db.execute(select(Vendor).where(Vendor.id == UUID(supplier_id)))).scalar_one_or_none()
    if not supplier:
        raise HTTPException(404, "Supplier not found")
    count = (await db.execute(select(func.count(Negotiation.id)))).scalar() or 0
    negotiation = Negotiation(
        negotiation_number=f"NEG-{datetime.utcnow().strftime('%Y%m%d')}-{count + 1:05d}",
        builder_id=current_user.id,
        supplier_id=supplier.id,
        product_id=UUID(payload["product_id"]) if payload.get("product_id") else None,
        product_name=product_name,
        category=payload.get("category"),
        quantity=payload.get("quantity", 1),
        unit=payload.get("unit", "piece"),
        requested_discount=requested_discount,
        value=payload.get("value", 0),
        status="pending",
    )
    db.add(negotiation)
    await db.commit()
    await db.refresh(negotiation)
    row = (await db.execute(select(Negotiation).where(Negotiation.id == negotiation.id))).scalar_one()
    return _neg_dict(row)


@router.get("/")
async def list_my_negotiations(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List the current user's (builder or supplier) negotiations."""
    vendor = (await db.execute(select(Vendor).where(Vendor.user_id == current_user.id))).scalar_one_or_none()
    if vendor:
        rows = (await db.execute(select(Negotiation).where(Negotiation.supplier_id == vendor.id))).scalars().all()
    else:
        rows = (await db.execute(select(Negotiation).where(Negotiation.builder_id == current_user.id))).scalars().all()
    return {"negotiations": [_neg_dict(n) for n in rows]}


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
    note = payload.get("note", "")
    if action in ("accept",):
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
    db.add(NegotiationCounterOffer(
        negotiation_id=n.id, offered_by=side,
        discount_percent=dp if dp is not None else n.requested_discount,
        note=note,
    ))
    await db.commit()
    row = (await db.execute(sa_select(Negotiation).where(Negotiation.id == n.id))).scalar_one()
    return _neg_dict(row)
