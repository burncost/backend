"""Procurement Intelligence Service — DB-verified comparison, history, range,
quotation analysis, savings, and procurement score.

All prices trace to the DB (material_rates / material_rate_history / products).
No Gemini-invented prices. When DB data is insufficient, quality-based flags
("insufficient_data") are returned instead of invented numbers.
"""
import logging
import asyncio
import time
from typing import Dict, List, Optional, Any
from decimal import Decimal
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.services.price_service import normalize_state

logger = logging.getLogger(__name__)

# Phase 10: tiny in-process TTL cache for verified offers. Keyed by
# (description, city) so repeated price lookups skip the DB for a short window.
_VERIFIED_OFFERS_CACHE: Dict[tuple, tuple] = {}
_VERIFIED_OFFERS_TTL = 60
_VERIFIED_OFFERS_LOCK = asyncio.Lock()


class ProcurementIntelligenceService:
    """DB-verified procurement intelligence operations."""

    def __init__(self, pg_db: Optional[AsyncSession] = None):
        self.pg_db = pg_db

    # ── Comparison ─────────────────────────────────────────────────────────

    async def compare_prices(
        self, description: str, quantity: float = 1.0, city: str = "Abuja"
    ) -> Dict[str, Any]:
        """
        Compare verified DB offers for a material.
        Returns offers with unit price, total procurement cost (incl. shipping),
        and provenance. Falls back to a clearly-flagged ai_estimate when no
        verified DB price exists.
        """
        from app.services.price_service import PriceService

        # Verified DB offers from material_rates.
        verified = await self._verified_offers(description, city)
        if verified:
            offers = []
            for offer in verified:
                shipping = float(offer.get("shipping_fee") or 0)
                unit = float(offer["rate"])
                # Respect the supplier's minimum order quantity: bill the MOQ
                # when the requested quantity is below it.
                moq = float(offer.get("minimum_order_quantity") or 1)
                billable_qty = max(quantity, moq)
                total = round(billable_qty * unit + shipping, 2)
                offers.append({
                    **offer,
                    "quantity": quantity,
                    "billable_quantity": billable_qty,
                    "subtotal": round(billable_qty * unit, 2),
                    "shipping_fee": shipping,
                    "total_procurement_cost": total,
                })
            return {
                "source": "database",
                "verified": True,
                "description": description,
                "city": city,
                "quantity": quantity,
                "offers": offers,
                "best_price": min(o["total_procurement_cost"] for o in offers),
                "explanation": (
                    f"{len(offers)} verified DB offer(s) for '{description}' in {city}. "
                    "Total procurement cost includes shipping."
                ),
            }

        # Flagged AI-estimate fallback (never presented as verified).
        estimate = await PriceService().get_rate(description, city)
        if estimate:
            unit = float(estimate["rate"])
            total = round(quantity * unit, 2)
            return {
                "source": "ai_estimate",
                "verified": False,
                "confidence": estimate.get("confidence", 0.3),
                "description": description,
                "city": city,
                "quantity": quantity,
                "offers": [{
                    "rate": unit,
                    "unit": estimate.get("unit", ""),
                    "product_name": estimate.get("product_name", description),
                    "price_source": "ai_estimate",
                    "verified": False,
                    "total_procurement_cost": total,
                }],
                "best_price": total,
                "insufficient_data": True,
                "explanation": (
                    f"BurnCost does not currently have a verified price for '{description}' "
                    f"in {city}. The figure shown is an AI estimate (unverified) so a "
                    "total can still be computed. A demand alert will be raised for "
                    "suppliers to provide real prices."
                ),
            }

        return {
            "source": "unavailable",
            "verified": False,
            "description": description,
            "city": city,
            "offers": [],
            "insufficient_data": True,
            "explanation": (
                f"No verified DB price or estimate available for '{description}' in {city}."
            ),
        }

    async def _verified_offers(self, description: str, city: str) -> List[Dict[str, Any]]:
        """Return verified material_rates offers for a description in a city.

        Enriches each offer with real procurement cost fields from the `products`
        table (shipping_fee, minimum_order_quantity, estimated_delivery_days)
        where a matching product exists. Falls back to zero/defaults when no
        product mapping is found. Results are cached in-process for _VERIFIED_OFFERS_TTL.
        """
        if self.pg_db is None:
            return []
        key = (description.lower(), city.lower())
        now = time.monotonic()

        # Fast path: serve from cache when fresh.
        cached = _VERIFIED_OFFERS_CACHE.get(key)
        if cached is not None and now - cached[0] < _VERIFIED_OFFERS_TTL:
            return list(cached[1])

        # Prevent thundering herd: one fetch at a time per key.
        async with _VERIFIED_OFFERS_LOCK:
            cached = _VERIFIED_OFFERS_CACHE.get(key)
            if cached is not None and now - cached[0] < _VERIFIED_OFFERS_TTL:
                return list(cached[1])

            offers = await self._fetch_verified_offers(description, city)
            _VERIFIED_OFFERS_CACHE[key] = (time.monotonic(), offers)
            return offers

    async def _fetch_verified_offers(self, description: str, city: str) -> List[Dict[str, Any]]:
        """Query DB and build verified offers (uncached)."""
        from app.models.material_rate import MaterialRate
        from app.models.product import Product
        try:
            result = await self.pg_db.execute(
                select(MaterialRate)
                .where(MaterialRate.state == normalize_state(city))
                .order_by(MaterialRate.material_name)
            )
            rates = result.scalars().all()
            desc_lower = description.lower()
            matches = [
                r for r in rates
                if desc_lower in r.material_name.lower()
                or any(word in r.material_name.lower() for word in desc_lower.split() if len(word) > 3)
            ]
            offers = []
            for r in matches:
                # Enrich with product logistics fields (shipping, MOQ, lead time).
                product = await self._match_product(r.material_name)
                shipping_fee = float(product.shipping_fee or 0) if product else 0.0
                moq = float(product.minimum_order_quantity or 1) if product else 1.0
                lead_days = int(product.estimated_delivery_days or 0) if product else 0
                offers.append({
                    "rate": float(r.current_price),
                    "unit": r.unit,
                    "product_name": r.material_name,
                    "supplier_id": str(r.supplier_id) if r.supplier_id else None,
                    "city": r.state or city,
                    "last_verified_at": str(r.updated_at or r.verified_at or ""),
                    "price_source": "database",
                    "verified": True,
                    "shipping_fee": shipping_fee,
                    "minimum_order_quantity": moq,
                    "estimated_delivery_days": lead_days,
                })
            return offers
        except Exception as e:
            logger.warning(f"_verified_offers failed: {e}")
            return []

    async def _match_product(self, material_name: str):
        """Find the first product whose name matches the material (for logistics)."""
        try:
            from app.models.product import Product
            result = await self.pg_db.execute(
                select(Product).where(Product.name.ilike(f"%{material_name}%")).limit(1)
            )
            return result.scalar_one_or_none()
        except Exception:
            return None

    # ── Price range (verified, from material_rates) ────────────────────────

    async def get_price_range(self, description: str, city: str = "Abuja") -> Dict[str, Any]:
        offers = await self._verified_offers(description, city)
        if not offers:
            return {
                "description": description,
                "city": city,
                "range": None,
                "insufficient_data": True,
                "explanation": "No verified DB price found for this item in this location.",
            }
        prices = [o["rate"] for o in offers]
        return {
            "description": description,
            "city": city,
            "range": {"min": min(prices), "max": max(prices), "count": len(prices)},
            "sufficient_data": len(prices) >= 3,
            "explanation": (
                f"Verified range based on {len(prices)} DB offer(s). "
                + ("Consider more data for higher confidence." if len(prices) < 3 else "")
            ),
        }

    # ── Price history (from material_rate_history) ─────────────────────────

    async def get_price_history(self, description: str, city: str = "Abuja", limit: int = 12) -> Dict[str, Any]:
        if self.pg_db is None:
            return {"description": description, "history": [], "insufficient_history": True}
        from app.models.material_rate import MaterialRate, MaterialRateHistory
        try:
            result = await self.pg_db.execute(
                select(MaterialRate, MaterialRateHistory)
                .join(MaterialRateHistory, MaterialRateHistory.rate_id == MaterialRate.id)
                .where(MaterialRate.state == normalize_state(city))
                .order_by(MaterialRateHistory.recorded_at.desc())
                .limit(limit)
            )
            rows = result.all()
            desc_lower = description.lower()
            history = []
            for rate, hist in rows:
                if desc_lower in rate.material_name.lower():
                    history.append({
                        "price": float(hist.price),
                        "recorded_at": str(hist.recorded_at),
                        "source": hist.source or "database",
                    })
            if not history:
                return {
                    "description": description,
                    "city": city,
                    "history": [],
                    "insufficient_history": True,
                    "explanation": "Insufficient price history for this item. Show data will improve as rates are updated.",
                }
            prices = [h["price"] for h in history]
            trend = (
                "down" if prices and len(prices) > 1 and prices[-1] < prices[0]
                else "up" if prices and len(prices) > 1 and prices[-1] > prices[0]
                else "stable"
            )
            return {
                "description": description,
                "city": city,
                "history": history,
                "insufficient_history": False,
                "trend": trend,
                "min": min(prices),
                "max": max(prices),
                "latest": prices[0] if prices else None,
            }
        except Exception as e:
            logger.warning(f"get_price_history failed: {e}")
            return {"description": description, "history": [], "insufficient_history": True, "error": str(e)}

    # ── Quotation analysis (DB-verified inflation flags) ───────────────────

    async def analyse_quotation(
        self,
        quoted_items: List[Dict[str, Any]],
        supplier_name: Optional[str] = None,
        user_id: Optional[str] = None,
        city: str = "Abuja",
    ) -> Dict[str, Any]:
        """
        Compare quoted line items against verified DB rates.
        Flag "potentially inflated" ONLY when there is sufficient DB-verified
        comparison data. Never invents market rates or accuses fraud.
        """
        inflated = 0
        fair = 0
        sufficient = 0
        items = []
        total_quoted = 0.0
        total_market = 0.0
        estimated_items = []

        for line in quoted_items:
            description = line.get("description", "")
            quantity = float(line.get("quantity", 0))
            quoted_rate = float(line.get("quoted_rate", 0))
            total_quoted += quantity * quoted_rate

            # DB-verified rate only — never an estimate, so inflation flags
            # are never based on invented prices.
            verified = await self._verified_offer_rate(description, city)
            if verified:
                market_rate = verified["rate"]
                total_market += quantity * market_rate
                deviation = abs(quoted_rate - market_rate) / market_rate * 100 if market_rate > 0 else 0
                sufficient += 1
                if deviation > 25:
                    status = "potentially_inflated"
                    inflated += 1
                else:
                    status = "fair"
                    fair += 1
                items.append({
                    "description": description,
                    "quantity": quantity,
                    "quoted_rate": quoted_rate,
                    "market_rate": market_rate,
                    "deviation_pct": round(deviation, 1),
                    "status": status,
                    "price_source": "database",
                    "verified": True,
                })
            else:
                items.append({
                    "description": description,
                    "quantity": quantity,
                    "quoted_rate": quoted_rate,
                    "market_rate": None,
                    "status": "unverified",
                    "price_source": "unavailable",
                    "verified": False,
                })
                estimated_items.append({
                    "description": description,
                    "quantity": quantity,
                    "unit": line.get("unit", ""),
                })

        # Raise demand alerts for unverified items.
        demand_alerts = 0
        if estimated_items and self.pg_db is not None:
            from app.services.price_service import PriceService
            ps = PriceService(pg_db=self.pg_db)
            demand_alerts = await ps.notify_vendors(
                estimated_items,
                project_title="Quotation analysis",
                user_id=user_id or "",
            )

        # Persist potentially-inflated items to price_anomalies so the Price
        # Integrity review workflow (admin) can act on them.
        persisted_flags = await self._persist_price_anomalies(
            inflated_items=[i for i in items if i.get("status") == "potentially_inflated"],
            supplier_name=supplier_name,
        )

        overall_status = (
            "flagged" if inflated > 0 and sufficient >= 1
            else "fair" if fair > 0 and inflated == 0
            else "unverified"
        )
        return {
            "supplier_name": supplier_name,
            "overall_status": overall_status,
            "total_quoted": round(total_quoted, 2),
            "total_market": round(total_market, 2),
            "total_overcharge": round(max(total_quoted - total_market, 0), 2),
            "inflated_count": inflated,
            "fair_count": fair,
            "unverified_count": len(items) - inflated - fair,
            "sufficient_data_items": sufficient,
            "items": items,
            "demand_alerts_created": demand_alerts,
            "price_anomalies_persisted": persisted_flags,
            "explanation": (
                "Flags are based on verified DB prices only. "
                "Items without sufficient DB comparison data are marked unverified, not inflated."
            ),
        }

    async def _persist_price_anomalies(
        self,
        inflated_items: List[Dict[str, Any]],
        supplier_name: Optional[str] = None,
    ) -> int:
        """Insert each potentially-inflated item as a PriceAnomaly row. Returns count persisted."""
        if not inflated_items or self.pg_db is None:
            return 0
        from app.models.price_boq import PriceAnomaly
        import uuid
        count = 0
        for item in inflated_items:
            try:
                self.pg_db.add(PriceAnomaly(
                    id=uuid.uuid4(),
                    anomaly_number=f"ANOM-{uuid.uuid4().hex[:12].upper()}",
                    item_name=item.get("description", "")[:500],
                    supplier_name=supplier_name,
                    market_price=float(item.get("market_rate") or 0),
                    quoted_price=float(item.get("quoted_rate") or 0),
                    variance_pct=float(item.get("deviation_pct") or 0),
                    status="flagged",
                    detected_at=datetime.utcnow(),
                ))
                count += 1
            except Exception as e:
                logger.warning(f"Failed to persist price anomaly for '{item.get('description')}': {e}")
        try:
            await self.pg_db.commit()
        except Exception as e:
            logger.warning(f"Price anomaly commit failed: {e}")
            try:
                await self.pg_db.rollback()
            except Exception:
                pass
        return count

    async def _verified_offer_rate(self, description: str, city: str) -> Optional[Dict[str, Any]]:
        offers = await self._verified_offers(description, city)
        if not offers:
            return None
        return offers[0]

    # ── Savings engine (DB-based) ──────────────────────────────────────────

    async def calculate_savings(
        self,
        items: List[Dict[str, Any]],
        supplier_a: Dict[str, Any],
        supplier_b: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Compare two suppliers' quoted baskets against a DB-verified baseline.

        Baseline preference:
          1. `boq_analysis_items.potential_saving` (per-item persisted savings
             from a prior BOQ analysis) when the item description matches a DB row.
          2. Otherwise the verified DB market rate (material_rates).
        Never AI-invented — every figure traces to the DB.
        """
        baseline = 0.0
        total_a = 0.0
        total_b = 0.0
        line_savings = []
        baseline_source_items = {"boq_analysis": 0, "market_rate": 0}

        for line in items:
            description = line.get("description", "")
            quantity = float(line.get("quantity", 0))

            # 1. Prefer persisted BOQ-analysis potential_saving as the baseline.
            potential_saving = await self._boq_potential_saving(description)
            if potential_saving is not None:
                baseline += float(potential_saving)
                baseline_source_items["boq_analysis"] += 1
                line_market = None
            else:
                # 2. Fall back to verified DB market rate.
                verified = await self._verified_offer_rate(description, "Abuja")
                market = float(verified["rate"]) if verified else 0.0
                baseline += quantity * market
                baseline_source_items["market_rate"] += 1
                line_market = market

            rate_a = float(supplier_a.get(description, 0) or 0)
            rate_b = float(supplier_b.get(description, 0) or 0)
            total_a += quantity * rate_a
            total_b += quantity * rate_b

            line_savings.append({
                "description": description,
                "quantity": quantity,
                "market_rate": line_market,
                "baseline_potential_saving": potential_saving,
                "supplier_a_rate": rate_a,
                "supplier_b_rate": rate_b,
            })

        return {
            "baseline_market_total": round(baseline, 2),
            "supplier_a_total": round(total_a, 2),
            "supplier_b_total": round(total_b, 2),
            "savings_vs_market_a": round(baseline - total_a, 2),
            "savings_vs_market_b": round(baseline - total_b, 2),
            "recommended_supplier": "a" if total_a <= total_b else "b",
            "line_items": line_savings,
            "baseline_source": baseline_source_items,
            "note": (
                "Baseline uses persisted BOQ-analysis potential savings where available; "
                "otherwise verified DB market rates. All figures trace to the database."
            ),
        }

    async def _boq_potential_saving(self, description: str) -> Optional[float]:
        """Return the latest persisted BOQ-analysis potential_saving for an item description, if any."""
        if self.pg_db is None:
            return None
        try:
            from app.models.price_boq import BOQAnalysisItem
            result = await self.pg_db.execute(
                select(BOQAnalysisItem.potential_saving)
                .where(BOQAnalysisItem.item_name.ilike(f"%{description}%"))
                .order_by(BOQAnalysisItem.id.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            return float(row) if row is not None else None
        except Exception as e:
            logger.warning(f"_boq_potential_saving failed: {e}")
            return None

    # ── Procurement score (explainable, DB-based) ──────────────────────────

    async def get_procurement_score(
        self,
        items: List[Dict[str, Any]],
        city: str = "Abuja",
        boq_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Explainable 0-100 procurement score derived from DB-verified data.
        Factors: price competitiveness, data sufficiency, and quantity confidence.
        When a `boq_id` is provided, the score is persisted into `boq_analyses`
        so it feeds the BOQ Analysis admin workflow.
        """
        verified_share = 0
        price_below_market = 0
        total_items = len(items) or 1

        for line in items:
            description = line.get("description", "")
            quoted = float(line.get("quoted_rate", 0))
            verified = await self._verified_offer_rate(description, city)
            if verified:
                verified_share += 1
                if 0 < quoted <= verified["rate"]:
                    price_below_market += 1

        verified_pct = verified_share / total_items
        competitive_pct = price_below_market / total_items

        # Weighted, explainable score.
        price_score = competitive_pct * 60.0
        sufficiency_score = verified_pct * 30.0
        quantity_score = 10.0 if total_items >= 1 else 0.0
        total_score = min(100, round(price_score + sufficiency_score + quantity_score, 1))
        grade = "A" if total_score >= 85 else "B" if total_score >= 70 else "C" if total_score >= 50 else "D"

        result = {
            "score": total_score,
            "grade": grade,
            "breakdown": {
                "price_competitiveness": round(competitive_pct * 100, 1),
                "data_sufficiency": round(verified_pct * 100, 1),
                "quantity_completeness": round(total_items, 1),
            },
            "weights": {"price": 60, "sufficiency": 30, "quantity": 10},
            "explanation": (
                f"Score based on {verified_share}/{total_items} items with verified DB prices "
                f"and {price_below_market} competitive against market."
            ),
        }

        # Persist score into boq_analyses when linked to a BOQ / project.
        if boq_id:
            persisted = await self._persist_boq_analysis(
                boq_id=boq_id,
                total_score=total_score,
                total_items=total_items,
                verified_share=verified_share,
                created_by=created_by,
            )
            result["boq_analysis_persisted"] = persisted

        return result

    async def _persist_boq_analysis(
        self,
        boq_id: str,
        total_score: float,
        total_items: int,
        verified_share: int,
        created_by: Optional[str] = None,
    ) -> bool:
        """Upsert a BOQAnalysis row carrying the procurement score."""
        if self.pg_db is None:
            return False
        try:
            from app.models.price_boq import BOQAnalysis
            from sqlalchemy import text
            import uuid
            # Use a stable boq_number derived from the source BOQ id.
            boq_number = f"SCORE-{str(boq_id).replace('-', '')[:20].upper()}"
            # Upsert on boq_number.
            await self.pg_db.execute(
                text("""
                    INSERT INTO boq_analyses
                        (id, boq_number, title, status, created_by, version,
                         confidence, total_items, flagged_items, total_value,
                         quoted_value, potential_savings, avg_variance, created_at)
                    VALUES
                        (:id, :boq_number, :title, 'completed', :created_by, 1,
                         :confidence, :total_items, 0, 0, 0, 0, 0, now())
                    ON CONFLICT (boq_number) DO UPDATE SET
                        confidence = EXCLUDED.confidence,
                        total_items = EXCLUDED.total_items,
                        created_by = EXCLUDED.created_by,
                        status = 'completed'
                """),
                {
                    "id": uuid.uuid4(),
                    "boq_number": boq_number,
                    "title": f"Procurement score for {boq_id}",
                    "created_by": created_by or "",
                    "confidence": float(total_score),
                    "total_items": int(total_items),
                },
            )
            await self.pg_db.commit()
            return True
        except Exception as e:
            logger.warning(f"_persist_boq_analysis failed: {e}")
            try:
                await self.pg_db.rollback()
            except Exception:
                pass
            return False
