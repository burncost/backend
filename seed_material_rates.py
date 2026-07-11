"""
Seed script: Populate MongoDB material_rates collection with Nigerian market rates.
Run: python seed_material_rates.py
"""
import asyncio
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient

# ── Nigerian Market Rates (2026) ──────────────────────────────────────────────

MATERIAL_RATES = [
    # ── Cement ──
    {"product_code": "CEM-001", "name": "Dangote Cement 42.5R (50kg)", "category": "cement", "unit": "bag", "unit_price": 5200, "city": "Abuja", "supplier": "Dangote", "brand": "Dangote"},
    {"product_code": "CEM-002", "name": "BUA Cement 42.5R (50kg)", "category": "cement", "unit": "bag", "unit_price": 5000, "city": "Abuja", "supplier": "BUA", "brand": "BUA"},
    {"product_code": "CEM-003", "name": "Lafarge Cement 42.5R (50kg)", "category": "cement", "unit": "bag", "unit_price": 5100, "city": "Abuja", "supplier": "Lafarge", "brand": "Lafarge"},
    {"product_code": "CEM-004", "name": "Dangote Cement 42.5R (50kg)", "category": "cement", "unit": "bag", "unit_price": 5500, "city": "Lagos", "supplier": "Dangote", "brand": "Dangote"},
    {"product_code": "CEM-005", "name": "Dangote Cement 42.5R (50kg)", "category": "cement", "unit": "bag", "unit_price": 5600, "city": "Port Harcourt", "supplier": "Dangote", "brand": "Dangote"},
    {"product_code": "CEM-006", "name": "Dangote Cement 42.5R (50kg)", "category": "cement", "unit": "bag", "unit_price": 4800, "city": "Kano", "supplier": "Dangote", "brand": "Dangote"},

    # ── Blocks ──
    {"product_code": "BLK-001", "name": "9-inch Hollow Sandcrete Block", "category": "block", "unit": "m²", "unit_price": 8500, "city": "Abuja"},
    {"product_code": "BLK-002", "name": "6-inch Hollow Sandcrete Block", "category": "block", "unit": "m²", "unit_price": 7200, "city": "Abuja"},
    {"product_code": "BLK-003", "name": "9-inch Solid Sandcrete Block", "category": "block", "unit": "m²", "unit_price": 9500, "city": "Abuja"},
    {"product_code": "BLK-004", "name": "9-inch Hollow Sandcrete Block", "category": "block", "unit": "m²", "unit_price": 9000, "city": "Lagos"},
    {"product_code": "BLK-005", "name": "9-inch Hollow Sandcrete Block", "category": "block", "unit": "m²", "unit_price": 7800, "city": "Ibadan"},

    # ── Aggregates ──
    {"product_code": "AGG-001", "name": "Sharp Sand (per tonne)", "category": "aggregate", "unit": "tonne", "unit_price": 8500, "city": "Abuja"},
    {"product_code": "AGG-002", "name": "Granite Chippings 3/4 inch (per tonne)", "category": "aggregate", "unit": "tonne", "unit_price": 18000, "city": "Abuja"},
    {"product_code": "AGG-003", "name": "Laterite (per tonne)", "category": "aggregate", "unit": "tonne", "unit_price": 4500, "city": "Abuja"},
    {"product_code": "AGG-004", "name": "Sharp Sand (per tonne)", "category": "aggregate", "unit": "tonne", "unit_price": 9000, "city": "Lagos"},
    {"product_code": "AGG-005", "name": "Granite Chippings 3/4 inch (per tonne)", "category": "aggregate", "unit": "tonne", "unit_price": 19500, "city": "Lagos"},

    # ── Reinforcement ──
    {"product_code": "REB-001", "name": "High Yield Rebar Y10 (12m)", "category": "rebar", "unit": "length", "unit_price": 3200, "city": "Abuja"},
    {"product_code": "REB-002", "name": "High Yield Rebar Y12 (12m)", "category": "rebar", "unit": "length", "unit_price": 4600, "city": "Abuja"},
    {"product_code": "REB-003", "name": "High Yield Rebar Y16 (12m)", "category": "rebar", "unit": "length", "unit_price": 8200, "city": "Abuja"},
    {"product_code": "REB-004", "name": "High Yield Rebar Y20 (12m)", "category": "rebar", "unit": "length", "unit_price": 12800, "city": "Abuja"},
    {"product_code": "REB-005", "name": "Binding Wire (per kg)", "category": "rebar", "unit": "kg", "unit_price": 1200, "city": "Abuja"},
    {"product_code": "REB-006", "name": "High Yield Rebar Y12 (12m)", "category": "rebar", "unit": "length", "unit_price": 4900, "city": "Lagos"},
    {"product_code": "REB-007", "name": "High Yield Rebar Y16 (12m)", "category": "rebar", "unit": "length", "unit_price": 8600, "city": "Lagos"},

    # ── Roofing ──
    {"product_code": "ROF-001", "name": "Longspan Aluminium Roofing 0.55mm", "category": "roofing", "unit": "m²", "unit_price": 4800, "city": "Abuja"},
    {"product_code": "ROF-002", "name": "Stone Coated Roofing Tile", "category": "roofing", "unit": "m²", "unit_price": 8500, "city": "Abuja"},
    {"product_code": "ROF-003", "name": "Timber Roof Truss (per m² coverage)", "category": "roofing", "unit": "m²", "unit_price": 4500, "city": "Abuja"},
    {"product_code": "ROF-004", "name": "Longspan Aluminium Roofing 0.55mm", "category": "roofing", "unit": "m²", "unit_price": 5100, "city": "Lagos"},
    {"product_code": "ROF-005", "name": "Longspan Aluminium Roofing 0.55mm", "category": "roofing", "unit": "m²", "unit_price": 5200, "city": "Port Harcourt"},

    # ── Ceiling ──
    {"product_code": "CEI-001", "name": "POP Ceiling (supply & install)", "category": "ceiling", "unit": "m²", "unit_price": 6500, "city": "Abuja"},
    {"product_code": "CEI-002", "name": "PVC Ceiling Board", "category": "ceiling", "unit": "m²", "unit_price": 3800, "city": "Abuja"},
    {"product_code": "CEI-003", "name": "Gypsum Ceiling Board", "category": "ceiling", "unit": "m²", "unit_price": 4500, "city": "Abuja"},

    # ── Tiles ──
    {"product_code": "TIL-001", "name": "Ceramic Floor Tile 600x600mm", "category": "finishes", "unit": "m²", "unit_price": 9500, "city": "Abuja"},
    {"product_code": "TIL-002", "name": "Ceramic Floor Tile 400x400mm", "category": "finishes", "unit": "m²", "unit_price": 7500, "city": "Abuja"},
    {"product_code": "TIL-003", "name": "Vitrified Tile 600x600mm", "category": "finishes", "unit": "m²", "unit_price": 13500, "city": "Abuja"},
    {"product_code": "TIL-004", "name": "Wall Tile 300x600mm", "category": "finishes", "unit": "m²", "unit_price": 8500, "city": "Abuja"},
    {"product_code": "TIL-005", "name": "Ceramic Floor Tile 600x600mm", "category": "finishes", "unit": "m²", "unit_price": 10000, "city": "Lagos"},
    {"product_code": "TIL-006", "name": "Ceramic Floor Tile 600x600mm", "category": "finishes", "unit": "m²", "unit_price": 8800, "city": "Ibadan"},

    # ── Paint ──
    {"product_code": "PNT-001", "name": "Premium Emulsion Paint (20L)", "category": "finishes", "unit": "bucket", "unit_price": 28000, "city": "Abuja"},
    {"product_code": "PNT-002", "name": "Gloss Paint (4L)", "category": "finishes", "unit": "tin", "unit_price": 8500, "city": "Abuja"},
    {"product_code": "PNT-003", "name": "Textured Paint (20L)", "category": "finishes", "unit": "bucket", "unit_price": 35000, "city": "Abuja"},

    # ── Doors ──
    {"product_code": "DOR-001", "name": "Flush Door (internal)", "category": "doors", "unit": "nr", "unit_price": 45000, "city": "Abuja"},
    {"product_code": "DOR-002", "name": "Security Door (metal)", "category": "doors", "unit": "nr", "unit_price": 85000, "city": "Abuja"},
    {"product_code": "DOR-003", "name": "Armoured Door", "category": "doors", "unit": "nr", "unit_price": 180000, "city": "Abuja"},

    # ── Windows ──
    {"product_code": "WIN-001", "name": "Aluminium Sliding Window 1.2x1.2m", "category": "windows", "unit": "nr", "unit_price": 75000, "city": "Abuja"},
    {"product_code": "WIN-002", "name": "Aluminium Sliding Window 0.9x1.2m", "category": "windows", "unit": "nr", "unit_price": 55000, "city": "Abuja"},
    {"product_code": "WIN-003", "name": "Louvre Window Frame (per m²)", "category": "windows", "unit": "m²", "unit_price": 18000, "city": "Abuja"},

    # ── Plumbing ──
    {"product_code": "PLB-001", "name": "WC Suite (low level)", "category": "plumbing", "unit": "nr", "unit_price": 45000, "city": "Abuja"},
    {"product_code": "PLB-002", "name": "Wash Hand Basin", "category": "plumbing", "unit": "nr", "unit_price": 18000, "city": "Abuja"},
    {"product_code": "PLB-003", "name": "Shower Fitting", "category": "plumbing", "unit": "nr", "unit_price": 12000, "city": "Abuja"},
    {"product_code": "PLB-004", "name": "Kitchen Sink (stainless)", "category": "plumbing", "unit": "nr", "unit_price": 35000, "city": "Abuja"},
    {"product_code": "PLB-005", "name": "PVC Pipe 4 inch (3m)", "category": "plumbing", "unit": "length", "unit_price": 4500, "city": "Abuja"},
    {"product_code": "PLB-006", "name": "PVC Pipe 2 inch (3m)", "category": "plumbing", "unit": "length", "unit_price": 2500, "city": "Abuja"},
    {"product_code": "PLB-007", "name": "WC Suite (low level)", "category": "plumbing", "unit": "nr", "unit_price": 48000, "city": "Lagos"},
    {"product_code": "PLB-008", "name": "WC Suite (low level)", "category": "plumbing", "unit": "nr", "unit_price": 42000, "city": "Ibadan"},

    # ── Electrical ──
    {"product_code": "ELE-001", "name": "PVC Conduit 20mm (3m)", "category": "electrical", "unit": "length", "unit_price": 1200, "city": "Abuja"},
    {"product_code": "ELE-002", "name": "Electrical Cable 2.5mm (per m)", "category": "electrical", "unit": "m", "unit_price": 450, "city": "Abuja"},
    {"product_code": "ELE-003", "name": "Electrical Cable 1.5mm (per m)", "category": "electrical", "unit": "m", "unit_price": 320, "city": "Abuja"},
    {"product_code": "ELE-004", "name": "Light Switch (single)", "category": "electrical", "unit": "nr", "unit_price": 1500, "city": "Abuja"},
    {"product_code": "ELE-005", "name": "Socket Outlet (double)", "category": "electrical", "unit": "nr", "unit_price": 2500, "city": "Abuja"},
    {"product_code": "ELE-006", "name": "Distribution Board (8-way)", "category": "electrical", "unit": "nr", "unit_price": 18000, "city": "Abuja"},

    # ── Labour rates ──
    {"product_code": "LAB-001", "name": "Masonry Labour (per m² blockwork)", "category": "labour", "unit": "m²", "unit_price": 4500, "city": "Abuja"},
    {"product_code": "LAB-002", "name": "Concrete Labour (per m³)", "category": "labour", "unit": "m³", "unit_price": 22000, "city": "Abuja"},
    {"product_code": "LAB-003", "name": "Tiling Labour (per m²)", "category": "labour", "unit": "m²", "unit_price": 3800, "city": "Abuja"},
    {"product_code": "LAB-004", "name": "Plastering Labour (per m²)", "category": "labour", "unit": "m²", "unit_price": 2200, "city": "Abuja"},
    {"product_code": "LAB-005", "name": "Painting Labour (per m²)", "category": "labour", "unit": "m²", "unit_price": 1200, "city": "Abuja"},
    {"product_code": "LAB-006", "name": "Roofing Labour (per m²)", "category": "labour", "unit": "m²", "unit_price": 2000, "city": "Abuja"},
    {"product_code": "LAB-007", "name": "Masonry Labour (per m² blockwork)", "category": "labour", "unit": "m²", "unit_price": 4800, "city": "Lagos"},
    {"product_code": "LAB-008", "name": "Masonry Labour (per m² blockwork)", "category": "labour", "unit": "m²", "unit_price": 4000, "city": "Ibadan"},
]


async def seed():
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    mongo_db = os.getenv("MONGO_DB", "burncost")
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[mongo_db]
    
    # Clear existing
    await db["material_rates"].delete_many({})
    
    # Insert seed data
    result = await db["material_rates"].insert_many(MATERIAL_RATES)
    print(f"✅ Inserted {len(result.inserted_ids)} material rates into '{mongo_db}.material_rates'")
    
    # Create indexes
    await db["material_rates"].create_index("product_code")
    await db["material_rates"].create_index("city")
    await db["material_rates"].create_index("category")
    await db["material_rates"].create_index([("city", 1), ("category", 1)])
    print("✅ Created indexes on product_code, city, category")
    
    # Summary
    categories = await db["material_rates"].distinct("category")
    cities = await db["material_rates"].distinct("city")
    print(f"📊 Categories: {categories}")
    print(f"📍 Cities: {cities}")
    
    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
