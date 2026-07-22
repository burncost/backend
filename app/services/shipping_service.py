"""
Shipping Service — zone + weight hybrid calculation.
Formula:
    zone = resolve_zone(vendor_origin, customer_destination)
    base_rate = zone.base_rate (or vendor override)
    billable_weight = max(actual_weight, volumetric_weight)
    weight_surcharge = tiered(billable_weight - zone.free_weight_kg)
    handling = zone.handling_fee
    total = base_rate + weight_surcharge + handling
"""
import logging
from typing import Dict, Any, List, Optional
from uuid import UUID
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.config import settings

logger = logging.getLogger(__name__)

# ── Weight tiers (progressive pricing) ──
# (up_to_kg, rate_per_kg)
WEIGHT_TIERS = [
    (50, 100),     # First 50kg: ₦100/kg
    (200, 75),     # 50-200kg: ₦75/kg
    (500, 50),     # 200-500kg: ₦50/kg
    (1000, 30),    # 500-1000kg: ₦30/kg
    (5000, 15),    # 1000-5000kg: ₦15/kg
    (None, 10),    # Above 5000kg: ₦10/kg
]


class ShippingService:
    """Calculates shipping fees using zone + weight hybrid model."""

    async def calculate_shipping(
        self,
        db: AsyncSession,
        vendor_id: UUID,
        items: List[Dict],  # product info + quantity
        destination_address_id: Optional[UUID] = None,
        destination_city: Optional[str] = None,
        destination_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Returns shipping fee breakdown.

        Dev mode: flat ₦5,000
        Prod mode: zone + weight hybrid calculation
        """
        if settings.DEBUG:
            return {
                "shipping_fee": 5000.00,
                "breakdown": {
                    "zone": "dev_flat_rate",
                    "base_rate": 5000,
                    "weight_surcharge": 0,
                    "handling_fee": 0,
                    "total_weight_kg": 0,
                    "estimated_days": "1-2",
                },
                "free_shipping": False,
            }

        # ── Resolve vendor origin ──
        vendor_origin = await self._get_vendor_origin(db, vendor_id)
        if not vendor_origin:
            logger.warning(f"No origin found for vendor {vendor_id}, using default")
            return self._default_shipping()

        # ── Resolve customer destination ──
        dest_city, dest_state = await self._resolve_destination(
            db, destination_address_id, destination_city, destination_state
        )
        if not dest_state:
            return self._default_shipping()

        # ── Resolve zone ──
        zone = await self._resolve_zone(db, vendor_origin["state"], dest_state)
        if not zone:
            zone = await self._get_default_zone(db)

        # ── Check vendor overrides ──
        override = await self._get_vendor_override(db, vendor_id, zone["id"])

        # ── Calculate weight ──
        total_weight = self._calculate_total_weight(items)
        volumetric_weight = self._calculate_volumetric_weight(items)
        billable_weight = max(total_weight, volumetric_weight)

        # ── Calculate costs ──
        base_rate = float(override["custom_base_rate"]) if override and override.get("custom_base_rate") else float(zone["base_rate"])
        rate_per_kg = float(override["custom_rate_per_kg"]) if override and override.get("custom_rate_per_kg") else float(zone["rate_per_kg"])

        # Weight surcharge using progressive tiers
        free_weight = float(zone["free_weight_kg"])
        chargeable_weight = max(0, billable_weight - free_weight)
        weight_surcharge = self._calculate_tiered_surcharge(chargeable_weight) if zone.get("id") else chargeable_weight * rate_per_kg

        handling_fee = float(zone["handling_fee"])

        # ── Check free shipping threshold ──
        shipping_fee = base_rate + weight_surcharge + handling_fee
        free_shipping = False
        if override and override.get("free_shipping_threshold"):
            threshold = float(override["free_shipping_threshold"])
            order_subtotal = sum(
                float(item.get("discount_price", item.get("base_price", 0))) * item.get("quantity", 0)
                for item in items
            )
            if order_subtotal >= threshold:
                shipping_fee = 0
                free_shipping = True

        return {
            "shipping_fee": round(shipping_fee, 2),
            "breakdown": {
                "zone": zone["name"],
                "zone_code": zone["code"],
                "base_rate": base_rate,
                "weight_surcharge": round(weight_surcharge, 2),
                "handling_fee": handling_fee,
                "total_weight_kg": round(total_weight, 1),
                "volumetric_weight_kg": round(volumetric_weight, 1),
                "billable_weight_kg": round(billable_weight, 1),
                "estimated_days": f"{zone['estimated_days_min']}-{zone['estimated_days_max']}",
            },
            "free_shipping": free_shipping,
        }

    async def _get_vendor_origin(self, db: AsyncSession, vendor_id: UUID) -> Optional[Dict]:
        """Get vendor's primary address as shipping origin."""
        result = await db.execute(
            text("""
                SELECT city, state FROM vendor_addresses
                WHERE vendor_id = :vid AND is_primary = TRUE
                LIMIT 1
            """),
            {"vid": str(vendor_id)},
        )
        addr = result.fetchone()
        if addr:
            return {"city": addr.city, "state": addr.state}

        # Fallback: vendor's business city/state
        result = await db.execute(
            text("SELECT city, state FROM vendors WHERE id = :vid"),
            {"vid": str(vendor_id)},
        )
        vendor = result.fetchone()
        if vendor:
            return {"city": vendor.city, "state": vendor.state}
        return None

    async def _resolve_destination(
        self, db: AsyncSession,
        address_id: Optional[UUID],
        city: Optional[str],
        state: Optional[str],
    ) -> tuple:
        """Resolve customer destination from address or direct params."""
        if address_id:
            result = await db.execute(
                text("SELECT city, state FROM customer_addresses WHERE id = :aid"),
                {"aid": str(address_id)},
            )
            addr = result.fetchone()
            if addr:
                return (addr.city, addr.state)
        return (city, state)

    async def _resolve_zone(self, db: AsyncSession, origin_state: str, dest_state: str) -> Optional[Dict]:
        """Find matching shipping zone for origin→destination state pair."""
        # Same state
        if origin_state.lower() == dest_state.lower():
            result = await db.execute(
                text("SELECT id, name, code, base_rate, rate_per_kg, free_weight_kg, handling_fee, estimated_days_min, estimated_days_max FROM shipping_zones WHERE code = 'metro' AND is_active = TRUE")
            )
            zone = result.fetchone()
            if zone:
                return dict(zone._mapping)

        # Check zone mappings
        result = await db.execute(
            text("""
                SELECT sz.id, sz.name, sz.code, sz.base_rate, sz.rate_per_kg,
                       sz.free_weight_kg, sz.handling_fee, sz.estimated_days_min, sz.estimated_days_max
                FROM shipping_zone_mappings szm
                JOIN shipping_zones sz ON szm.zone_id = sz.id
                WHERE szm.origin_state = :ostate
                  AND (szm.origin_city IS NULL)
                  AND szm.destination_state = :dstate
                  AND sz.is_active = TRUE
                LIMIT 1
            """),
            {"ostate": origin_state, "dstate": dest_state},
        )
        zone = result.fetchone()
        if zone:
            return dict(zone._mapping)

        # Check reverse (destination→origin)
        result = await db.execute(
            text("""
                SELECT sz.id, sz.name, sz.code, sz.base_rate, sz.rate_per_kg,
                       sz.free_weight_kg, sz.handling_fee, sz.estimated_days_min, sz.estimated_days_max
                FROM shipping_zone_mappings szm
                JOIN shipping_zones sz ON szm.zone_id = sz.id
                WHERE szm.destination_state = :ostate
                  AND szm.origin_state = :dstate
                  AND sz.is_active = TRUE
                LIMIT 1
            """),
            {"ostate": origin_state, "dstate": dest_state},
        )
        zone = result.fetchone()
        if zone:
            return dict(zone._mapping)

        return None

    async def _get_default_zone(self, db: AsyncSession) -> Optional[Dict]:
        """Fallback to national zone."""
        result = await db.execute(
            text("SELECT id, name, code, base_rate, rate_per_kg, free_weight_kg, handling_fee, estimated_days_min, estimated_days_max FROM shipping_zones WHERE code = 'national' AND is_active = TRUE")
        )
        zone = result.fetchone()
        if zone:
            return dict(zone._mapping)
        return None

    async def _get_vendor_override(self, db: AsyncSession, vendor_id: UUID, zone_id: UUID) -> Optional[Dict]:
        """Get vendor-specific shipping override for a zone."""
        result = await db.execute(
            text("""
                SELECT custom_base_rate, custom_rate_per_kg, free_shipping_threshold
                FROM vendor_shipping_overrides
                WHERE vendor_id = :vid AND zone_id = :zid AND is_active = TRUE
                LIMIT 1
            """),
            {"vid": str(vendor_id), "zid": str(zone_id)},
        )
        row = result.fetchone()
        return dict(row._mapping) if row else None

    def _calculate_total_weight(self, items: List[Dict]) -> float:
        """Sum actual weight of all items."""
        total = 0.0
        for item in items:
            weight = float(item.get("weight", 0) or 0)
            qty = item.get("quantity", 1)
            total += weight * qty
        return total

    def _calculate_volumetric_weight(self, items: List[Dict]) -> float:
        """Calculate volumetric weight (LxWxH / 5000)."""
        total = 0.0
        for item in items:
            length = float(item.get("length", 0) or 0)
            width = float(item.get("width", 0) or 0)
            height = float(item.get("height", 0) or 0)
            qty = item.get("quantity", 1)
            vol_weight = (length * width * height * qty) / 5000
            total += vol_weight
        return total

    def _calculate_tiered_surcharge(self, weight_kg: float) -> float:
        """Calculate weight surcharge using progressive tiers."""
        remaining = weight_kg
        prev_limit = 0
        total_surcharge = 0.0

        for up_to, rate in WEIGHT_TIERS:
            if remaining <= 0:
                break
            tier_limit = up_to if up_to is not None else float("inf")
            tier_amount = min(remaining, tier_limit - prev_limit)
            if tier_amount > 0:
                total_surcharge += tier_amount * rate
                remaining -= tier_amount
            prev_limit = tier_limit

        return total_surcharge

    def _default_shipping(self) -> Dict[str, Any]:
        """Default shipping when origin/destination can't be resolved."""
        return {
            "shipping_fee": 10000.00,
            "breakdown": {
                "zone": "default",
                "base_rate": 10000,
                "weight_surcharge": 0,
                "handling_fee": 0,
                "total_weight_kg": 0,
                "estimated_days": "3-5",
            },
            "free_shipping": False,
        }