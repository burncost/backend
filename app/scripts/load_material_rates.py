"""MongoDB material_rates loader.

Seeds/populates the `material_rates` MongoDB collection that the
PriceService/PriceEngine reads from for BOQ enrichment.

Usage (from Backend/):
    python -m app.scripts.load_material_rates [--clear] [--file path.json]

Optionally pass --clear to wipe existing docs before loading. Without --file,
a built-in seed list of common Nigerian construction materials is used.
"""
import asyncio
import argparse
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Default seed catalogue: (name, category, unit, Abuja price NGN, cities)
SEED_MATERIALS: List[Dict[str, Any]] = [
    # Cement & Binding
    {"name": "Cement - Portland Type I (50kg)", "category": "cement", "unit": "bag", "unit_price": 12500, "cities": ["Abuja", "Lagos", "Port Harcourt", "Ibadan", "Kano"]},
    {"name": "Cement - Portland 42.5R (50kg)", "category": "cement", "unit": "bag", "unit_price": 13200, "cities": ["Abuja", "Lagos"]},

    # Aggregates & Sand
    {"name": "Sharp Sand", "category": "sand", "unit": "m3", "unit_price": 38000, "cities": ["Abuja", "Lagos", "Port Harcourt", "Ibadan"]},
    {"name": "Laterite", "category": "fill", "unit": "m3", "unit_price": 22000, "cities": ["Abuja", "Ibadan", "Lagos"]},
    {"name": "Granite 3/4 inch (aggregate)", "category": "aggregate", "unit": "m3", "unit_price": 45000, "cities": ["Abuja", "Lagos", "Port Harcourt"]},

    # Blocks
    {"name": "Sandcrete block 225mm (9 inch)", "category": "block", "unit": "nr", "unit_price": 1300, "cities": ["Abuja", "Lagos", "Port Harcourt", "Ibadan", "Kano"]},
    {"name": "Sandcrete block 150mm (6 inch)", "category": "block", "unit": "nr", "unit_price": 1100, "cities": ["Abuja", "Lagos", "Port Harcourt", "Ibadan", "Kano"]},

    # Steel / Rebar
    {"name": "High yield rebar Y16 (12m)", "category": "rebar", "unit": "length", "unit_price": 14500, "cities": ["Abuja", "Lagos", "Port Harcourt"]},
    {"name": "High yield rebar Y12 (12m)", "category": "rebar", "unit": "length", "unit_price": 8200, "cities": ["Abuja", "Lagos", "Port Harcourt"]},
    {"name": "Binding wire (1kg roll)", "category": "rebar", "unit": "kg", "unit_price": 1800, "cities": ["Abuja", "Lagos"]},

    # Roofing
    {"name": "Aluminium longspan roofing 0.55mm", "category": "roofing", "unit": "m2", "unit_price": 6000, "cities": ["Abuja", "Lagos", "Port Harcourt"]},
    {"name": "Stone coated tile", "category": "roofing", "unit": "m2", "unit_price": 11000, "cities": ["Abuja", "Lagos"]},
    {"name": "Timber roof trusses", "category": "roofing", "unit": "m2", "unit_price": 6500, "cities": ["Abuja", "Lagos", "Ibadan"]},

    # Finishes
    {"name": "Ceramic floor tile 600x600mm", "category": "tiles", "unit": "m2", "unit_price": 13300, "cities": ["Abuja", "Lagos", "Port Harcourt"]},
    {"name": "Porcelain tile 600x600mm", "category": "tiles", "unit": "m2", "unit_price": 15500, "cities": ["Abuja", "Lagos"]},
    {"name": "POP ceiling", "category": "ceiling", "unit": "m2", "unit_price": 6500, "cities": ["Abuja", "Lagos"]},
    {"name": "PVC ceiling", "category": "ceiling", "unit": "m2", "unit_price": 4800, "cities": ["Abuja", "Lagos", "Ibadan"]},

    # Doors & Windows
    {"name": "Flush door (internal) 0.8x2.1m", "category": "doors", "unit": "nr", "unit_price": 65000, "cities": ["Abuja", "Lagos"]},
    {"name": "Security door (metal) 0.9x2.1m", "category": "doors", "unit": "nr", "unit_price": 120000, "cities": ["Abuja", "Lagos", "Port Harcourt"]},
    {"name": "Aluminium sliding window 1.2x1.2m", "category": "windows", "unit": "nr", "unit_price": 95000, "cities": ["Abuja", "Lagos"]},

    # Paint
    {"name": "Emulsion paint premium (5L)", "category": "paint", "unit": "can", "unit_price": 34000, "cities": ["Abuja", "Lagos", "Port Harcourt"]},
    {"name": "Gloss paint (5L)", "category": "paint", "unit": "can", "unit_price": 42000, "cities": ["Abuja", "Lagos"]},

    # Plumbing
    {"name": "WC suite (low level)", "category": "plumbing", "unit": "nr", "unit_price": 65000, "cities": ["Abuja", "Lagos", "Port Harcourt"]},
    {"name": "Wash hand basin", "category": "plumbing", "unit": "nr", "unit_price": 25000, "cities": ["Abuja", "Lagos"]},
    {"name": "PVC pipe 4 inch (drainage)", "category": "plumbing", "unit": "m", "unit_price": 3500, "cities": ["Abuja", "Lagos", "Ibadan"]},
    {"name": "PVC pipe 1/2 inch (water)", "category": "plumbing", "unit": "m", "unit_price": 2500, "cities": ["Abuja", "Lagos"]},

    # Electrical
    {"name": "Cable 2.5mm (conduit wiring)", "category": "electrical", "unit": "m", "unit_price": 1200, "cities": ["Abuja", "Lagos"]},
    {"name": "LED bulb 9W", "category": "electrical", "unit": "nr", "unit_price": 1800, "cities": ["Abuja", "Lagos", "Port Harcourt"]},
    {"name": "Double socket outlet", "category": "electrical", "unit": "nr", "unit_price": 9500, "cities": ["Abuja", "Lagos", "Port Harcourt"]},
]


def _normalize_price(price: float, city: str) -> float:
    """Apply the same city multiplier used by PriceService."""
    factors = {
        "Abuja": 1.0,
        "Lagos": 1.05,
        "Port Harcourt": 1.08,
        "Benin City": 0.95,
        "Ibadan": 0.92,
        "Kano": 0.88,
        "Kaduna": 0.90,
        "Enugu": 0.93,
    }
    return round(price * factors.get(city, 1.0), 2)


def build_docs(seed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Expand seed materials into per-city product_code documents."""
    docs: List[Dict[str, Any]] = []
    idx = 0
    for mat in seed:
        cities = mat.get("cities") or ["Abuja"]
        base_price = float(mat["unit_price"])
        for city in cities:
            idx += 1
            product_code = f"MAT-{idx:03d}"
            docs.append({
                "product_code": product_code,
                "name": mat["name"],
                "category": mat["category"],
                "unit": mat["unit"],
                "unit_price": _normalize_price(base_price, city),
                "city": city,
                "rate": _normalize_price(base_price, city),  # legacy alias used by regional-rate repo
                "materialName": mat["name"],
                "state": city,
                "supplier": None,
                "brand": None,
                "last_updated": datetime.utcnow().isoformat(),
                "isActive": True,
                "source": "seed_loader",
            })
    return docs


async def load(clear: bool = False, file_path: Optional[str] = None) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.config import settings

    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.MONGO_DB]
    collection = db["material_rates"]

    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            seed = json.load(f)
        docs = build_docs(seed)
    else:
        docs = build_docs(SEED_MATERIALS)

    if clear:
        deleted = await collection.delete_many({})
        logger.info(f"Cleared {deleted.deleted_count} existing documents")

    inserted = 0
    for doc in docs:
        result = await collection.replace_one(
            {"product_code": doc["product_code"]},
            doc,
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted += 1

    client.close()
    logger.info(f"Loaded {len(docs)} material rates into MongoDB ('{settings.MONGO_DB}'.material_rates), {inserted} new")
    return len(docs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed MongoDB material_rates collection")
    parser.add_argument("--clear", action="store_true", help="Wipe existing docs before loading")
    parser.add_argument("--file", type=str, default=None, help="Path to JSON seed file (list of materials)")
    args = parser.parse_args()

    asyncio.run(load(clear=args.clear, file_path=args.file))


if __name__ == "__main__":
    main()