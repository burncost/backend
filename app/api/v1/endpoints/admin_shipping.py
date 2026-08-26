"""Phase 6 admin shipping endpoints — manage ShippingZone & VendorShippingOverride."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from uuid import UUID
from typing import Optional

from app.core.database import get_db
from app.api.deps import require_roles
from app.models.shipping_zone import ShippingZone
from app.models.vendor_shipping_override import VendorShippingOverride

router = APIRouter()
admin_guard = require_roles("manager", "support", "marketing")


def _zone_dict(z: ShippingZone) -> dict:
    return {
        "id": str(z.id),
        "name": z.name,
        "code": z.code,
        "base_rate": float(z.base_rate or 0),
        "rate_per_kg": float(z.rate_per_kg or 0),
        "free_weight_kg": float(z.free_weight_kg or 0),
        "handling_fee": float(z.handling_fee or 0),
        "is_active": bool(z.is_active),
    }


@router.get("/shipping-zones")
async def admin_list_zones(current_user=Depends(admin_guard), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ShippingZone).order_by(ShippingZone.name))).scalars().all()
    return {"zones": [_zone_dict(z) for z in rows], "count": len(rows)}


@router.post("/shipping-zones")
async def admin_create_zone(payload: dict, current_user=Depends(admin_guard), db: AsyncSession = Depends(get_db)):
    name = payload.get("name")
    code = payload.get("code")
    base_rate = payload.get("base_rate")
    if not name or not code or base_rate is None:
        raise HTTPException(400, "name, code and base_rate required")
    zone = ShippingZone(name=name, code=code.upper(), base_rate=base_rate,
                        rate_per_kg=payload.get("rate_per_kg", 0),
                        free_weight_kg=payload.get("free_weight_kg", 10),
                        handling_fee=payload.get("handling_fee", 0))
    db.add(zone)
    await db.commit()
    await db.refresh(zone)
    return _zone_dict(zone)


@router.patch("/shipping-zones/{zone_id}")
async def admin_update_zone(zone_id: UUID, payload: dict, current_user=Depends(admin_guard), db: AsyncSession = Depends(get_db)):
    zone = (await db.execute(select(ShippingZone).where(ShippingZone.id == zone_id))).scalar_one_or_none()
    if not zone:
        raise HTTPException(404, "Shipping zone not found")
    for field in ("name", "code", "base_rate", "rate_per_kg", "free_weight_kg", "handling_fee", "is_active"):
        if field in payload:
            setattr(zone, field, payload[field])
    await db.commit()
    await db.refresh(zone)
    return _zone_dict(zone)


@router.delete("/shipping-zones/{zone_id}")
async def admin_deactivate_zone(zone_id: UUID, current_user=Depends(admin_guard), db: AsyncSession = Depends(get_db)):
    zone = (await db.execute(select(ShippingZone).where(ShippingZone.id == zone_id))).scalar_one_or_none()
    if not zone:
        raise HTTPException(404, "Shipping zone not found")
    zone.is_active = False
    await db.commit()
    return {"deactivated": str(zone.id)}


@router.get("/shipping-overrides")
async def admin_list_overrides(current_user=Depends(admin_guard), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(VendorShippingOverride))).scalars().all()
    return {"overrides": [
        {
            "id": str(o.id),
            "vendor_id": str(o.vendor_id),
            "zone_id": str(o.zone_id),
            "custom_base_rate": float(o.custom_base_rate) if o.custom_base_rate is not None else None,
            "custom_rate_per_kg": float(o.custom_rate_per_kg) if o.custom_rate_per_kg is not None else None,
            "free_shipping_threshold": float(o.free_shipping_threshold) if o.free_shipping_threshold is not None else None,
            "is_active": bool(o.is_active),
        }
        for o in rows
    ]}