"""
Price Service
Interfaces with the building materials database to:
  1. Look up current market prices per material/product code
  2. Enrich BOQ line items with real DB prices
  3. Flag items where BOQ price deviates significantly from DB price
  4. Internet search fallback when no DB price exists
  5. Vendor notification for out-of-stock items

Supports both MongoDB (material_rates collection) and PostgreSQL (material_rates table).
"""
import os
import math
import json
import re
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.services.gemini_client import get_gemini_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# DOMAIN MODELS
# ─────────────────────────────────────────

@dataclass
class DBProduct:
    product_code: str
    name: str
    category: str
    unit: str
    unit_price: float        # NGN
    city: str
    supplier: Optional[str] = None
    brand: Optional[str] = None
    last_updated: Optional[str] = None


# ─────────────────────────────────────────
# MATERIAL CODE MAPPING
# ─────────────────────────────────────────

ITEM_TO_CATEGORY: Dict[str, str] = {
    "SUB": "foundation",
    "BLK": "block",
    "CON": "concrete",
    "REB": "rebar",
    "ROF": "roofing",
    "CEI": "ceiling",
    "FIN": "finishes",
    "DOR": "doors",
    "WIN": "windows",
    "ELE": "electrical",
    "PLB": "plumbing",
    "STR": "structural",
    "STA": "staircase",
    "PRE": "preliminaries",
}

DESCRIPTION_KEYWORDS: Dict[str, List[str]] = {
    "sandcrete block 225mm": ["sandcrete", "225mm", "block"],
    "sandcrete block 150mm": ["sandcrete", "150mm", "block"],
    "high yield rebar": ["reinforcement", "Y16", "rebar"],
    "aluminium longspan": ["aluminium", "longspan", "roofing"],
    "stone coated tile": ["stone coated", "tile", "roofing"],
    "ceramic tile 600x600": ["ceramic tile", "600x600"],
    "porcelain tile": ["porcelain tile", "600x600"],
    "pop ceiling": ["POP", "ceiling", "plaster"],
    "pvc ceiling": ["PVC", "ceiling"],
    "flush door": ["flush door", "internal door"],
    "security door": ["security door", "metal door"],
    "aluminium window": ["aluminium", "window", "sliding"],
    "electrical point": ["electrical", "wiring", "fitting"],
    "wc suite": ["WC", "toilet", "sanitary"],
    "wash hand basin": ["wash hand basin", "basin"],
    "cement": ["cement", "opc"],
    "sharp sand": ["sharp sand", "sand"],
    "granite": ["granite", "aggregate"],
    "laterite": ["laterite", "fill"],
    "binding wire": ["binding wire"],
    "timber": ["timber", "wood"],
    "paint emulsion": ["emulsion", "paint"],
    "paint gloss": ["gloss", "paint"],
    "pvc pipe": ["PVC", "pipe"],
    "cable": ["cable", "electrical"],
}

# City pricing multipliers (relative to Abuja baseline)
CITY_FACTORS: Dict[str, float] = {
    "Abuja": 1.0,
    "Lagos": 1.05,
    "Port Harcourt": 1.08,
    "Benin City": 0.95,
    "Ibadan": 0.92,
    "Kano": 0.88,
    "Kaduna": 0.90,
    "Enugu": 0.93,
}

# Finish level multipliers
FINISH_MULTIPLIERS: Dict[str, float] = {
    "economy": 0.75,
    "standard": 1.0,
    "premium": 1.35,
    "luxury": 1.80,
}


# ─────────────────────────────────────────
# PRICE ENGINE — real DB queries + internet fallback
# ─────────────────────────────────────────

class PriceEngine:
    """
    PriceEngine queries the MongoDB material_rates collection for real prices.
    Falls back to internet search via Gemini if no DB match.
    """

    def __init__(self, mongo_db: Optional[AsyncIOMotorDatabase] = None):
        self.mongo_db = mongo_db

    async def get_rate(self, description: str, city: str = "Abuja") -> Optional[Dict[str, Any]]:
        """Get best matching rate for a BOQ item description."""
        # Try keyword matching
        for desc_key, keywords in DESCRIPTION_KEYWORDS.items():
            if desc_key.lower() in description.lower():
                product = await self._search_by_keywords(keywords, city)
                if product:
                    return product

        # Broader search
        words = [w for w in description.lower().split() if len(w) > 3]
        if words:
            product = await self._search_by_keywords(words[:4], city)
            if product:
                return product

        return None

    async def _search_by_keywords(self, keywords: List[str], city: str) -> Optional[Dict[str, Any]]:
        """Search material_rates by keywords and city."""
        if self.mongo_db is not None:
            try:
                # Try exact city match first
                pipeline = [
                    {"$match": {"city": city, "$or": [
                        {"name": {"$regex": kw, "$options": "i"}} for kw in keywords
                    ]}},
                    {"$sort": {"last_updated": -1}},
                    {"$limit": 1},
                ]
                cursor = self.mongo_db["material_rates"].aggregate(pipeline)
                results = await cursor.to_list(length=1)
                if results:
                    doc = results[0]
                    return {
                        "rate": float(doc["unit_price"]),
                        "unit": doc.get("unit", ""),
                        "product_name": doc.get("name", ""),
                        "product_code": doc.get("product_code", ""),
                        "source": "database",
                        "city": doc.get("city", city),
                    }

                # Fallback: any city
                pipeline = [
                    {"$match": {"$or": [
                        {"name": {"$regex": kw, "$options": "i"}} for kw in keywords
                    ]}},
                    {"$sort": {"last_updated": -1}},
                    {"$limit": 1},
                ]
                cursor = self.mongo_db["material_rates"].aggregate(pipeline)
                results = await cursor.to_list(length=1)
                if results:
                    doc = results[0]
                    return {
                        "rate": float(doc["unit_price"]),
                        "unit": doc.get("unit", ""),
                        "product_name": doc.get("name", ""),
                        "product_code": doc.get("product_code", ""),
                        "source": "database",
                        "city": doc.get("city", "unknown"),
                    }
            except Exception as e:
                logger.warning(f"MongoDB query failed: {e}")

        # No mock fallback — return None so caller can try internet search
        return None

    async def internet_search_rate(self, description: str, city: str = "Abuja") -> Optional[Dict[str, Any]]:
        """
        Search the internet for current market price using Gemini.
        Returns rate info if found, None otherwise.
        """
        try:
            client = get_gemini_client()
            prompt = f"""You are a Nigerian quantity surveyor with access to current market prices.
Search your knowledge for the current market price of this construction material in {city}, Nigeria:

Material: {description}
City: {city}

Return ONLY valid JSON with this exact structure:
{{
  "rate": number (price in NGN per unit),
  "unit": "string (e.g. m², m³, nr, bag, kg, length, ls)",
  "product_name": "string (full product name)",
  "product_code": "string (auto-generated code like MAT-001)",
  "source": "internet_search",
  "city": "{city}",
  "confidence": number (0-1 how confident you are in this price)
}}

If you don't know the price, return: {{"rate": null, "source": "not_found"}}"""

            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=[prompt],
                config={"temperature": 0.1, "max_output_tokens": 1024},
            )

            text = response.text
            if not text:
                return None

            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
            if match:
                result = json.loads(match.group(1))
            else:
                match = re.search(r'\{[\s\S]*\}', text)
                if match:
                    result = json.loads(match.group(0))
                else:
                    return None

            if result.get("rate") is None:
                return None

            return {
                "rate": float(result["rate"]),
                "unit": result.get("unit", ""),
                "product_name": result.get("product_name", description),
                "product_code": result.get("product_code", ""),
                "source": "internet_search",
                "city": city,
                "confidence": result.get("confidence", 0.5),
            }
        except Exception as e:
            logger.warning(f"Internet search failed for '{description}': {e}")
            return None

    async def get_prices_by_category(self, category: str, city: str = "Abuja") -> List[Dict[str, Any]]:
        """Get all prices for a category in a city."""
        if self.mongo_db is not None:
            try:
                cursor = self.mongo_db["material_rates"].find(
                    {"category": category, "city": city}
                ).sort("name", 1)
                results = await cursor.to_list(length=100)
                return [
                    {
                        "product_code": r.get("product_code"),
                        "name": r.get("name"),
                        "unit": r.get("unit"),
                        "unit_price": float(r.get("unit_price", 0)),
                        "city": r.get("city"),
                    }
                    for r in results
                ]
            except Exception as e:
                logger.warning(f"MongoDB query failed: {e}")

        return []

    async def get_city_factor(self, city: str) -> float:
        return CITY_FACTORS.get(city, 1.0)

    async def get_finish_level_multiplier(self, level: str) -> float:
        return FINISH_MULTIPLIERS.get(level, 1.0)


# ─────────────────────────────────────────
# PRICE SERVICE
# ─────────────────────────────────────────

class PriceService:
    """
    Retrieves product prices from database and enriches BOQ items.
    Supports MongoDB (material_rates collection) and PostgreSQL.
    Falls back to internet search via Gemini if no DB match.
    No mock/hardcoded data is used.
    """

    def __init__(self, mongo_db: Optional[AsyncIOMotorDatabase] = None, pg_db: Optional[AsyncSession] = None):
        self.mongo_db = mongo_db
        self.pg_db = pg_db
        self._price_cache: Optional[Dict[str, DBProduct]] = None
        self._cache_timestamp: Optional[datetime] = None
        self.engine = PriceEngine(mongo_db)

    async def _load_prices_from_mongo(self) -> Dict[str, DBProduct]:
        """Load prices from MongoDB material_rates collection."""
        if self.mongo_db is None:
            return {}

        products = {}
        try:
            cursor = self.mongo_db["material_rates"].find({})
            async for doc in cursor:
                p = DBProduct(
                    product_code=doc.get("product_code", doc.get("code", "")),
                    name=doc.get("name", ""),
                    category=doc.get("category", ""),
                    unit=doc.get("unit", ""),
                    unit_price=float(doc.get("unit_price", doc.get("price", 0))),
                    city=doc.get("city", "Abuja"),
                    supplier=doc.get("supplier"),
                    brand=doc.get("brand"),
                    last_updated=str(doc.get("last_updated", "")),
                )
                products[p.product_code] = p
            logger.info(f"Loaded {len(products)} products from MongoDB material_rates")
        except Exception as e:
            logger.warning(f"Failed to load from MongoDB: {e}")

        return products

    async def _load_prices(self) -> Dict[str, DBProduct]:
        """
        Load prices from available DB only.
        No mock data fallback.
        """
        if self._price_cache is not None:
            return self._price_cache

        if self.mongo_db is not None:
            db_products = await self._load_prices_from_mongo()
            if db_products:
                self._price_cache = db_products
                return self._price_cache

        self._price_cache = {}
        logger.info("No DB prices loaded — will use internet search fallback")
        return self._price_cache

    async def search_products(
        self, query: str, city: str = "Abuja", limit: int = 10
    ) -> List[DBProduct]:
        """Search products by name/keyword."""
        prices = await self._load_prices()
        query_lower = query.lower()
        results = []

        for p in prices.values():
            if query_lower in p.name.lower() or query_lower in p.category.lower():
                results.append(p)

        results.sort(key=lambda x: (
            0 if x.city.lower() == city.lower() else 1,
            x.name.lower().find(query_lower) if query_lower in x.name.lower() else 999,
        ))

        return results[:limit]

    async def get_rate(
        self, description: str, city: str = "Abuja", quantity: float = 1.0
    ) -> Optional[Dict[str, Any]]:
        """
        Get the best matching rate for a BOQ item description.
        Tries DB first, then internet search.
        Returns rate info including unit price and source.
        """
        prices = await self._load_prices()
        description_lower = description.lower()

        # Try keyword matching against DB
        for desc_key, keywords in DESCRIPTION_KEYWORDS.items():
            if desc_key.lower() in description_lower:
                product = await self._search_by_keywords(keywords, city)
                if product:
                    return {
                        "rate": product.unit_price,
                        "unit": product.unit,
                        "product_name": product.name,
                        "product_code": product.product_code,
                        "source": "database",
                        "city": product.city,
                    }

        # Try broader search
        words = [w for w in description_lower.split() if len(w) > 3]
        if words:
            product = await self._search_by_keywords(words[:4], city)
            if product:
                return {
                    "rate": product.unit_price,
                    "unit": product.unit,
                    "product_name": product.name,
                    "product_code": product.product_code,
                    "source": "database",
                    "city": product.city,
                }

        # Internet search fallback
        logger.info(f"DB price not found for '{description}' — trying internet search")
        internet_result = await self.engine.internet_search_rate(description, city)
        if internet_result:
            return internet_result

        return None

    async def get_city_factor(self, city: str) -> float:
        """Get pricing multiplier for a city."""
        return CITY_FACTORS.get(city, 1.0)

    async def get_finish_level_multiplier(self, level: str) -> float:
        """Get pricing multiplier for finish level."""
        return FINISH_MULTIPLIERS.get(level, 1.0)

    async def _search_by_keywords(self, keywords: List[str], city: str) -> Optional[DBProduct]:
        """Find best matching product from keywords."""
        prices = await self._load_prices()
        candidates = []

        for product in prices.values():
            city_match = product.city.lower() == city.lower()
            score = sum(
                1 for kw in keywords
                if kw.lower() in product.name.lower() or
                   kw.lower() in product.category.lower()
            )
            if score > 0:
                candidates.append((score + (2 if city_match else 0), product))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    async def get_price_by_code(self, product_code: str) -> Optional[DBProduct]:
        prices = await self._load_prices()
        return prices.get(product_code)

    async def get_prices_by_category(self, category: str, city: str = "Abuja") -> List[DBProduct]:
        prices = await self._load_prices()
        return [
            p for p in prices.values()
            if category.lower() in p.category.lower()
        ]

    async def enrich_boq_elements(
        self, elements: List[Dict], city: str = "Abuja"
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """
        Match BOQ line items against the DB price catalogue.
        Returns (enriched_elements, price_discrepancies, out_of_stock_items)
        """
        enriched = []
        discrepancies = []
        out_of_stock = []

        for element in elements:
            enriched_items = []
            for item in element.get("items", []):
                enriched_item, discrepancy, oos = await self._match_item(item, city)
                enriched_items.append(enriched_item)
                if discrepancy:
                    discrepancies.append(discrepancy)
                if oos:
                    out_of_stock.append(oos)
            element["items"] = enriched_items
            enriched.append(element)

        return enriched, discrepancies, out_of_stock

    async def _match_item(self, item: Dict, city: str) -> Tuple[Dict, Optional[Dict], Optional[Dict]]:
        """
        Try to match a BOQ item to a DB product.
        Returns (item_with_db_info, discrepancy_or_None, out_of_stock_info_or_None)
        """
        description = item.get("description", "").lower()
        item_code = item.get("item_code", "")

        # Search by description keywords
        matching_product = None
        for desc_key, keywords in DESCRIPTION_KEYWORDS.items():
            if any(kw.lower() in description for kw in [desc_key]):
                matching_product = await self._search_by_keywords(keywords, city)
                if matching_product:
                    break

        if not matching_product:
            words = [w for w in description.split() if len(w) > 3]
            if words:
                matching_product = await self._search_by_keywords(words[:4], city)

        if matching_product:
            item["db_price_matched"] = True
            item["db_product_name"] = matching_product.name
            item["db_product_code"] = matching_product.product_code
            item["db_unit_price"] = matching_product.unit_price
            item["out_of_stock"] = False

            gemini_rate = item.get("adjusted_rate", 0)
            if gemini_rate > 0:
                deviation_pct = abs(gemini_rate - matching_product.unit_price) / matching_product.unit_price * 100
                if deviation_pct > 25:
                    old_amount = item.get("amount", 0)
                    item["adjusted_rate"] = matching_product.unit_price
                    item["amount"] = round(item.get("quantity", 0) * matching_product.unit_price, 2)
                    item["rate_source"] = "database"

                    discrepancy = {
                        "item_code": item_code,
                        "description": item.get("description", ""),
                        "gemini_rate": gemini_rate,
                        "db_rate": matching_product.unit_price,
                        "deviation_pct": round(deviation_pct, 1),
                        "action": "replaced_with_db_price",
                    }
                    return item, discrepancy, None
                else:
                    item["rate_source"] = "gemini_verified"
            else:
                item["rate_source"] = "database"
                item["adjusted_rate"] = matching_product.unit_price
                item["amount"] = round(item.get("quantity", 0) * matching_product.unit_price, 2)

            return item, None, None

        # Try internet search
        logger.info(f"No DB match for '{description}' — trying internet search")
        internet_result = await self.engine.internet_search_rate(description, city)
        if internet_result:
            item["db_price_matched"] = True
            item["db_product_name"] = internet_result.get("product_name", description)
            item["db_product_code"] = internet_result.get("product_code", "")
            item["db_unit_price"] = internet_result["rate"]
            item["adjusted_rate"] = internet_result["rate"]
            item["amount"] = round(item.get("quantity", 0) * internet_result["rate"], 2)
            item["rate_source"] = "internet_search"
            item["out_of_stock"] = False
            return item, None, None

        # No price found anywhere — mark as out of stock
        item["db_price_matched"] = False
        item["rate_source"] = "unavailable"
        item["out_of_stock"] = True
        item["vendor_notified"] = False

        oos_info = {
            "item_code": item_code,
            "description": item.get("description", ""),
            "quantity": item.get("quantity", 0),
            "unit": item.get("unit", ""),
            "city": city,
        }

        return item, None, oos_info

    async def notify_vendors(self, out_of_stock_items: List[Dict], project_title: str = "", user_id: str = "") -> int:
        """
        Create demand alert records in PostgreSQL for out-of-stock items.
        Returns count of alerts created.
        """
        if not self.pg_db:
            logger.warning("No PostgreSQL session — cannot notify vendors")
            return 0

        count = 0
        for item in out_of_stock_items:
            try:
                stmt = text("""
                    INSERT INTO demand_alerts
                        (item_description, city, quantity_needed, unit, project_title, requested_by, status, notified_vendors)
                    VALUES
                        (:description, :city, :quantity, :unit, :project_title, :user_id, 'pending', ARRAY[]::TEXT[])
                """)
                await self.pg_db.execute(stmt, {
                    "description": item.get("description", ""),
                    "city": item.get("city", "Abuja"),
                    "quantity": item.get("quantity", 0),
                    "unit": item.get("unit", ""),
                    "project_title": project_title,
                    "user_id": user_id,
                })
                count += 1
            except Exception as e:
                logger.warning(f"Failed to create demand alert for '{item.get('description')}': {e}")

        if count > 0:
            await self.pg_db.commit()
            logger.info(f"Created {count} demand alerts for out-of-stock items")

        return count

    def recalculate_totals(self, elements: List[Dict]) -> Dict:
        """Recompute element and summary totals after price enrichment."""
        grand_total = 0.0
        for element in elements:
            el_total = sum(item.get("amount") or 0 for item in element.get("items", []))
            element["element_total"] = round(el_total, 2)
            grand_total += el_total

        for element in elements:
            if grand_total > 0:
                element["cost_percentage_of_total"] = round(
                    element["element_total"] / grand_total * 100, 2
                )

        contingency = round(grand_total * 0.05, 2)
        vat = round((grand_total + contingency) * 0.075, 2)
        total = round(grand_total + contingency + vat, 2)

        return {
            "sub_total": round(grand_total, 2),
            "contingency_pct": 5,
            "contingency_amount": contingency,
            "vat_pct": 7.5,
            "vat_amount": vat,
            "total_contract_sum": total,
        }

    async def get_catalogue_summary(self) -> Dict:
        """Returns a summary of available products for frontend display."""
        prices = await self._load_prices()
        by_category: Dict[str, int] = {}
        for p in prices.values():
            by_category[p.category] = by_category.get(p.category, 0) + 1

        return {
            "total_products": len(prices),
            "by_category": by_category,
            "cities_covered": list(set(p.city for p in prices.values())),
        }
