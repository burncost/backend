"""
Seed script to populate the 20 BurnCost strategic categories with subcategories and platform margins.
Run with: python -m app.seed_categories
"""
import asyncio
import uuid
from app.core.database import AsyncSessionLocal
from app.models.category import Category

# ── Full 20-category master list ──────────────────────────────────────────────

CATEGORIES = [
    {
        "code": "CAT-001",
        "name": "Cement",
        "slug": "cement",
        "division": "Structure",
        "default_unit": "bag",
        "platform_margin": 1.50,          # fixed fee per bag, ~1-2% midpoint
        "fee_model": "fixed",
        "fee_fixed": 200,                  # ₦200/bag
        "quality_metric": "CQI",
        "subcategories": [
            "General Purpose Cement", "High Strength Cement", "Sulphate Resistant Cement",
            "White Cement", "Masonry Cement", "Oil Well Cement", "Bulk Cement",
            "Ready-Mix Cement", "Cement Additives",
        ],
    },
    {
        "code": "CAT-002",
        "name": "Reinforcement Steel",
        "slug": "reinforcement-steel",
        "division": "Structure",
        "default_unit": "tonne",
        "platform_margin": 1.25,          # 1.0-1.5% midpoint
        "fee_model": "percentage",
        "quality_metric": "SQI",
        "subcategories": [
            "TMT Rebars", "Mild Steel Bars", "High Yield Bars", "Wire Rods",
            "Binding Wire", "Steel Mesh", "Steel Plates", "Structural Steel",
            "Hollow Sections", "Angles & Channels",
        ],
    },
    {
        "code": "CAT-003",
        "name": "Fine Aggregates",
        "slug": "fine-aggregates",
        "division": "Structure",
        "default_unit": "trip",
        "platform_margin": 5.00,          # fixed fee per trip, midpoint ₦10,000/trip (stored as % for simplicity)
        "fee_model": "fixed",
        "fee_fixed": 10000,
        "subcategories": [
            "Sharp Sand", "Plaster Sand", "Filling Sand", "White Sand",
            "River Sand", "Laterite", "Top Soil", "Hardcore", "Quarry Dust",
        ],
    },
    {
        "code": "CAT-004",
        "name": "Coarse Aggregates",
        "slug": "coarse-aggregates",
        "division": "Structure",
        "default_unit": "trip",
        "platform_margin": 5.00,          # fixed fee per trip, midpoint ₦19,000/trip
        "fee_model": "fixed",
        "fee_fixed": 19000,
        "subcategories": [
            "Granite 1/2\"", "Granite 3/4\"", "Granite 1\"", "Granite 1-1/2\"",
            "Stone Dust", "Crusher Run", "Gravel", "Chippings", "Base Course",
        ],
    },
    {
        "code": "CAT-005",
        "name": "Masonry Products",
        "slug": "masonry-products",
        "division": "Structure",
        "default_unit": "piece",
        "platform_margin": 3.00,          # ₦10-100/block midpoint → ₦55, stored as %
        "fee_model": "fixed",
        "fee_fixed": 55,
        "quality_metric": "BQI",
        "subcategories": [
            '9" Hollow Blocks', '6" Hollow Blocks', 'Solid Blocks',
            "Interlocking Blocks", "Paving Blocks", "Kerbs",
            "Concrete Bricks", "Burnt Bricks", "Sandcrete Bricks",
        ],
    },
    {
        "code": "CAT-006",
        "name": "Burnt Bricks",
        "slug": "burnt-bricks",
        "division": "Structure",
        "default_unit": "piece",
        "platform_margin": 5.00,          # 3-7% midpoint
        "fee_model": "percentage",
        "subcategories": ["Premium Bricks"],
    },
    {
        "code": "CAT-007",
        "name": "Ceiling Systems",
        "slug": "ceiling-systems",
        "division": "Finishes",
        "default_unit": "sheet",
        "platform_margin": 5.50,          # 3-8% midpoint
        "fee_model": "percentage",
        "subcategories": [
            "PVC Ceiling", "POP", "Gypsum Board", "Acoustic Ceiling",
            "Suspended Ceiling", "Wooden Ceiling", "Mineral Fibre Ceiling",
            "Ceiling Accessories",
        ],
    },
    {
        "code": "CAT-008",
        "name": "Tiles & Flooring",
        "slug": "tiles-flooring",
        "division": "Finishes",
        "default_unit": "m2",
        "platform_margin": 6.00,          # 4-8% midpoint
        "fee_model": "percentage",
        "quality_metric": "FQI",
        "subcategories": [
            "Ceramic Tiles", "Porcelain Tiles", "Marble", "Granite Tiles",
            "Terrazzo", "Vinyl Flooring", "SPC Flooring", "Laminate Flooring",
            "Engineered Wood Flooring", "Solid Hardwood Flooring",
            "Bamboo Flooring", "Outdoor Decking", "Tile Adhesive, Grout & Spacers",
        ],
    },
    {
        "code": "CAT-009",
        "name": "Timber & Engineered Wood",
        "slug": "timber-engineered-wood",
        "division": "Finishes",
        "default_unit": "sheet",
        "platform_margin": 4.00,          # 2-6% midpoint
        "fee_model": "percentage",
        "quality_metric": "WQI",
        "subcategories": [
            "Hardwood & Softwood", "Marine Plywood & Commercial Plywood",
            "MDF & HDF", "MFC & OSB", "Particle Board & LVL",
            "Laminated & Finger Joint Timber", "Bamboo Boards & Veneers",
        ],
    },
    {
        "code": "CAT-010",
        "name": "Roofing Systems",
        "slug": "roofing-systems",
        "division": "Building Envelope",
        "default_unit": "sheet",
        "platform_margin": 5.00,          # 3-7% midpoint
        "fee_model": "percentage",
        "quality_metric": "RQI",
        "subcategories": [
            "Long Span & Step Tile Aluminium", "Stone-Coated & Zinc Roofing",
            "Galvanized & Fibre Cement Roofing", "Polycarbonate Roofing & Trusses",
            "Flashings, Gutters & Downpipes", "Roofing Screws, Ridge Caps & Valleys",
        ],
    },
    {
        "code": "CAT-011",
        "name": "Plumbing Systems",
        "slug": "plumbing-systems",
        "division": "MEP",
        "default_unit": "piece",
        "platform_margin": 4.50,          # 3-6% midpoint
        "fee_model": "percentage",
        "quality_metric": "PQI",
        "subcategories": [
            "PVC, PPR & HDPE Pipes", "UPVC, CPVC & Drainage Pipes",
            "Sewer & Pressure Pipes", "Valves & Fittings",
            "Water Pumps, Tanks & Borehole Accessories",
        ],
    },
    {
        "code": "CAT-012",
        "name": "Sanitary Ware",
        "slug": "sanitary-ware",
        "division": "MEP",
        "default_unit": "unit",
        "platform_margin": 5.50,          # 4-7% midpoint
        "fee_model": "percentage",
        "subcategories": [
            "Water Closets & Wash Basins", "Urinals & Bathtubs",
            "Shower Systems & Cabinets", "Mixers, Faucets & Sinks",
        ],
    },
    {
        "code": "CAT-013",
        "name": "Electrical Systems",
        "slug": "electrical-systems",
        "division": "MEP",
        "default_unit": "roll",
        "platform_margin": 4.50,          # 3-6% midpoint
        "fee_model": "percentage",
        "quality_metric": "EQI",
        "subcategories": [
            "Cables & Wires", "Conduits & Trunking",
            "Switches, Sockets & Distribution Boards",
            "Circuit Breakers & Lighting",
            "Transformers, Solar Cables & Smart Controls",
        ],
    },
    {
        "code": "CAT-014",
        "name": "Paints & Coatings",
        "slug": "paints-coatings",
        "division": "Finishes",
        "default_unit": "bucket",
        "platform_margin": 5.50,          # 3-8% midpoint
        "fee_model": "percentage",
        "quality_metric": "PQI",
        "subcategories": [
            "Emulsion, Silk & Matt", "Satin, Gloss & Textured Coatings",
            "Screeding Products & Primers", "Sealers & Waterproof Coatings",
            "Wood Finishes & Protective Coatings",
        ],
    },
    {
        "code": "CAT-015",
        "name": "Doors, Windows & Facades",
        "slug": "doors-windows-facades",
        "division": "Building Envelope",
        "default_unit": "unit",
        "platform_margin": 6.00,          # 4-8% midpoint
        "fee_model": "percentage",
        "quality_metric": "DFQI",
        "subcategories": [
            "Flush, Panel & Security Doors", "Fire Rated & Sliding Doors",
            "Glass, Aluminium & Wooden Doors", "Garage & Industrial Doors",
            "Aluminium Profiles", "Sliding & Casement Windows",
            "Curtain Walls & ACP Cladding", "Louvres, Skylights & Shop Fronts",
            "Mosquito Nets & Installation Hardware",
        ],
    },
    {
        "code": "CAT-016",
        "name": "Glass",
        "slug": "glass",
        "division": "Building Envelope",
        "default_unit": "m2",
        "platform_margin": 6.00,          # Inherited from Doors/Windows cluster
        "fee_model": "percentage",
        "subcategories": [
            "Float & Tempered Glass", "Laminated & Reflective Glass",
            "Low-E & Frosted Glass", "Mirror, Double & Decorative Glazing",
        ],
    },
    {
        "code": "CAT-017",
        "name": "Smart Building Systems",
        "slug": "smart-building-systems",
        "division": "Building Services",
        "default_unit": "system",
        "platform_margin": 6.00,          # 4-8% midpoint
        "fee_model": "percentage",
        "subcategories": [
            "CCTV, Access Control & Intercoms", "Video Doorbells & Fire Alarms",
            "Smart Lighting & Smart Home Hubs",
            "Network Equipment & Automation Systems",
        ],
    },
    {
        "code": "CAT-018",
        "name": "Solar & Renewable Energy",
        "slug": "solar-renewable-energy",
        "division": "Building Services",
        "default_unit": "system",
        "platform_margin": 4.50,          # 3-6% midpoint
        "fee_model": "percentage",
        "quality_metric": "SQI",
        "subcategories": [
            "Solar Panels & Inverters", "Lithium Batteries & Charge Controllers",
            "Mounting Systems & Solar Street Lights",
            "Hybrid Systems & Installation Accessories",
        ],
    },
    {
        "code": "CAT-019",
        "name": "Tools & Consumables",
        "slug": "tools-consumables",
        "division": "Finishes",
        "default_unit": "unit",
        "platform_margin": 6.00,          # 4-8% midpoint
        "fee_model": "percentage",
        "subcategories": [
            "Hand Tools", "PPE", "Power Tools",
        ],
    },
    {
        "code": "CAT-020",
        "name": "Equipment & Site Services",
        "slug": "equipment-site-services",
        "division": "External Works",
        "default_unit": "contract",
        "platform_margin": 6.50,          # 5-8% midpoint (Service fee)
        "fee_model": "service",
        "subcategories": [
            "Excavators", "Rollers", "Cranes", "Mixers",
            "Haulage Services", "Crane Services", "Borehole Services",
            "Testing Services",
        ],
    },
]


async def seed_categories():
    from app.core.database import AsyncSessionLocal, engine
    from app.models.category import Category
    from sqlalchemy import select, delete

    async with AsyncSessionLocal() as db:
        # Clear existing categories (optional — comment out for incremental seeding)
        await db.execute(delete(Category))
        await db.commit()

        created = 0
        for cat_data in CATEGORIES:
            # Create parent category
            parent = Category(
                id=uuid.uuid4(),
                name=cat_data["name"],
                slug=cat_data["slug"],
                division=cat_data.get("division", "General"),
                default_unit=cat_data.get("default_unit", "piece"),
                platform_margin=cat_data["platform_margin"],
                is_active=True,
                description=f"{cat_data['name']} — {cat_data.get('quality_metric', 'N/A') or 'N/A'}, Fee model: {cat_data['fee_model']}",
            )
            db.add(parent)
            await db.flush()

            # Create subcategories as children
            for sub_name in cat_data.get("subcategories", []):
                sub = Category(
                    id=uuid.uuid4(),
                    name=sub_name,
                    slug=f"{cat_data['slug']}-{sub_name.lower().replace(' ', '-').replace('/', '-').replace('"', '')}",
                    parent_id=parent.id,
                    division=cat_data.get("division", "General"),
                    default_unit=cat_data.get("default_unit", "piece"),
                    platform_margin=cat_data["platform_margin"],
                    is_active=True,
                )
                db.add(sub)

            created += 1

        await db.commit()
        print(f"✅ Seeded {created} parent categories with subcategories.")

    print("🎉 Category seeding complete.")


if __name__ == "__main__":
    asyncio.run(seed_categories())