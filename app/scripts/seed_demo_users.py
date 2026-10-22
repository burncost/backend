"""Idempotent demo-account seeder: builder (customer), vendor and driver.

Usage (from Backend/):
    python -m app.scripts.seed_demo_users
    python -m app.scripts.seed_demo_users --reset          # also re-set the demo passwords
    python -m app.scripts.seed_demo_users --tokens 5000    # different token balance
    python -m app.scripts.seed_demo_users --password 'Custom-Pass1'

Each account is created "demo ready", i.e. nothing is left in a half-onboarded state
that would hide a feature behind a verification gate:

  builder -> active customer + profile + token balance (BOQ, chat, export)
  vendor  -> active vendor row, verified status, enterprise tier (no cap), so products
             are publicly listed and the tier/payout surfaces are unlocked
  driver  -> active driver profile, availability ON, all four vehicle photos present and
             payout details filled, so /deliveries/offers actually returns jobs

Re-running is safe: rows are looked up by email and updated in place, never duplicated.
Passwords are hashed with the app's own hasher (passlib/bcrypt).
"""
import argparse
import asyncio
from datetime import datetime

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.core.security import get_password_hash
from app.models.category import Category
from app.models.driver import DriverProfile
from app.models.product import Product, ProductStatus
from app.models.token_usage import TokenTransaction, TokenUsage
from app.models.user import User, UserProfile
from app.models.vendor import Vendor

# Demo credentials. Change via --password if these ever leak publicly.
DEMO_PASSWORD = {
    "builder": "Builder2026!",
    "vendor": "Vendor2026!",
    "driver": "Driver2026!",
}

# Placeholder photos: the delivery gate only needs four non-empty URLs, and these
# render in the driver UI so the "add vehicle photos" banner stays hidden.
_PHOTO = "https://placehold.co/1200x900/jpeg?text=Demo+Vehicle+{}"

# Demand alerts are matched to a vendor by CITY ONLY (see GET /vendors/me/demand-alerts,
# which filters `LOWER(TRIM(city)) = LOWER(TRIM(vendor.city))`). Live demo broadcasts are
# raised in Abuja, so a Lagos-based demo vendor sees an empty demand feed. Keep these aligned
# with where the demo BOQs are generated.
DEMO_VENDOR_CITY = "Abuja"
DEMO_VENDOR_STATE = "FCT"

# A broadcast only notifies vendors that own an ACTIVE product whose name / description /
# meta_keywords matches the line keywords, so the demo vendor needs a real catalogue or
# it never receives a "New demand near you" notification nor a bid thread.
DEMO_PRODUCTS = [
    {
        "sku": "DEMO-CEM-50",
        "name": "Dangote Cement 50kg (42.5R)",
        "category": "Cement",
        "description": "General purpose 42.5R bagged cement for blockwork, concrete and plastering. Delivered per 50kg bag.",
        "short_description": "42.5R bagged cement, 50kg",
        "meta_keywords": "cement, binder, 42.5r, 50kg, bag, concrete, mortar, plaster",
        "unit_of_measure": "bag",
        "base_price": 9500,
        "quantity": 1200,
    },
    {
        "sku": "DEMO-BLK-225",
        "name": "Sandcrete Block 225mm (9 inch)",
        "category": '9" Hollow Blocks',
        "description": "9 inch (225mm) hollow sandcrete block for external walls. Cured 7 days, sold per piece.",
        "short_description": "9 inch sandcrete block (225mm)",
        "meta_keywords": "block, blocks, sandcrete, 9-inch, 9inch, 225mm, 225, masonry, wall",
        "unit_of_measure": "piece",
        "base_price": 850,
        "quantity": 4000,
    },
    {
        "sku": "DEMO-BLK-150",
        "name": "Sandcrete Block 150mm (6 inch)",
        "category": '6" Hollow Blocks',
        "description": "6 inch (150mm) hollow sandcrete block for internal partitions. Sold per piece.",
        "short_description": "6 inch sandcrete block (150mm)",
        "meta_keywords": "block, blocks, sandcrete, 6-inch, 6inch, 150mm, 150, partition",
        "unit_of_measure": "piece",
        "base_price": 700,
        "quantity": 4000,
    },
    {
        "sku": "DEMO-ROOF-LS",
        "name": "Longspan Aluminium Roofing Sheet 0.55mm",
        "category": "Galvanized & Fibre Cement Roofing",
        "description": "0.55mm aluminium longspan roofing sheet, cut to length. Price per metre.",
        "short_description": "0.55mm aluminium longspan sheet",
        "meta_keywords": "roof, roofing, longspan, aluminium, aluminum, sheet, sheets, 0.55mm",
        "unit_of_measure": "length",
        "base_price": 13500,
        "quantity": 600,
    },
    {
        "sku": "DEMO-ROOF-TILE",
        "name": "Stone Coated Roofing Tile",
        "category": "Galvanized & Fibre Cement Roofing",
        "description": "Stone coated steel roofing tile, 0.45mm gauge, for pitched roofs. Sold per piece.",
        "short_description": "Stone coated roofing tile",
        "meta_keywords": "roof, roofing, tile, tiles, stone coated, sheet",
        "unit_of_measure": "piece",
        "base_price": 9800,
        "quantity": 900,
    },
    {
        "sku": "DEMO-TILE-600",
        "name": "Ceramic Floor Tile 600x600mm",
        "category": "Ceramic Tiles",
        "description": "600x600mm porcelain floor tile, matt finish, 4 pieces per box (1.44sqm).",
        "short_description": "600x600mm floor tile",
        "meta_keywords": "tile, tiles, ceramic, porcelain, floor tile, 600x600mm, 600x600",
        "unit_of_measure": "box",
        "base_price": 8900,
        "quantity": 500,
    },
    {
        "sku": "DEMO-TILE-300",
        "name": "Ceramic Wall Tile 300x600mm",
        "category": "Ceramic Tiles",
        "description": "300x600mm glazed wall tile for kitchen and bathroom walls, 6 pieces per box.",
        "short_description": "300x600mm wall tile",
        "meta_keywords": "tile, tiles, ceramic, wall tile, 300x600mm, 300x600",
        "unit_of_measure": "box",
        "base_price": 7600,
        "quantity": 500,
    },
    {
        "sku": "DEMO-TRUSS",
        "name": "Timber Roof Truss (6m span)",
        "category": "Hardwood & Softwood",
        "description": "Pre-fabricated timber roof truss for a 6m span, treated hardwood. Sold per truss.",
        "short_description": "Treated timber roof truss",
        "meta_keywords": "truss, trusses, timber, wood, roof, hardwood",
        "unit_of_measure": "piece",
        "base_price": 48000,
        "quantity": 120,
    },
    {
        "sku": "DEMO-PURLIN",
        "name": "Roof Purlin 2x4 Timber (4.8m)",
        "category": "Hardwood & Softwood",
        "description": "2x4 inch treated timber purlin, 4.8m length, for roof carcass. Price per length.",
        "short_description": "2x4 timber purlin, 4.8m",
        "meta_keywords": "purlin, purlins, rafter, rafters, timber, wood, roof",
        "unit_of_measure": "length",
        "base_price": 5200,
        "quantity": 800,
    },
    {
        "sku": "DEMO-GRANITE",
        "name": 'Granite 3/4" Chippings (per tonne)',
        "category": 'Granite 3/4"',
        "description": 'Crushed granite chippings, 3/4 inch grade, for concrete works. Priced per tonne.',
        "short_description": 'Granite 3/4" chippings',
        "meta_keywords": "granite, chippings, aggregate, coarse, 3/4, concrete",
        "unit_of_measure": "tonne",
        "base_price": 32000,
        "quantity": 300,
    },
]


DEMO_USERS = [
    {
        "label": "builder",
        "role": "customer",
        "email": "builder.demo@burncost.com",
        "phone_number": "+2348000000101",
        "first_name": "Bola",
        "last_name": "Builder",
        "business_name": "Bola Construction Ltd",
        "location": "Lagos",
    },
    {
        "label": "vendor",
        "role": "vendor",
        "email": "vendor.demo@burncost.com",
        "phone_number": "+2348000000102",
        "first_name": "Vera",
        "last_name": "Vendor",
        "business_name": "Demo BuildMart Ltd",
        "location": "Lagos",
    },
    {
        "label": "driver",
        "role": "driver",
        "email": "driver.demo@burncost.com",
        "phone_number": "+2348000000103",
        "first_name": "Dayo",
        "last_name": "Driver",
        "business_name": "",
        "location": "Lagos",
    },
]


def _mask(db_url: str) -> str:
    """Host/database only - never echo credentials into logs."""
    tail = db_url.split("://", 1)[-1].split("@", 1)[-1]
    return tail.split("?", 1)[0]

async def _upsert_user(session, spec: dict, password: str, reset_password: bool) -> User:
    user = (
        await session.execute(select(User).where(User.email == spec["email"]))
    ).scalar_one_or_none()

    if user is None:
        user = User(
            email=spec["email"],
            phone_number=spec["phone_number"],
            password_hash=get_password_hash(password),
            role=spec["role"],
            status="active",
            email_verified=True,
            phone_verified=True,
            auth_provider="email",
            password_changed_at=datetime.utcnow(),
        )
        session.add(user)
        await session.flush()
    else:
        # Keep a demo account usable even if it was suspended / half-registered.
        user.role = spec["role"]
        user.status = "active"
        user.email_verified = True
        user.phone_verified = True
        user.phone_number = user.phone_number or spec["phone_number"]
        if reset_password or not user.password_hash:
            user.password_hash = get_password_hash(password)
            user.password_changed_at = datetime.utcnow()

    profile = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user.id)
    profile.first_name = spec["first_name"]
    profile.last_name = spec["last_name"]
    profile.business_name = spec["business_name"] or None
    profile.location = spec["location"]
    session.add(profile)

    await session.commit()
    await session.refresh(user)
    return user


async def _ensure_vendor(session, user: User) -> Vendor:
    vendor = (
        await session.execute(select(Vendor).where(Vendor.user_id == user.id))
    ).scalar_one_or_none()
    if vendor is None:
        vendor = Vendor(user_id=user.id)
    vendor.business_name = "Demo BuildMart Ltd"
    vendor.business_type = "Building Materials"
    # Must match the city the demo demand broadcasts are raised in - the vendor
    # demand feed filters on vendor.city, so a mismatch shows an empty page.
    vendor.city = DEMO_VENDOR_CITY
    vendor.state = DEMO_VENDOR_STATE
    vendor.business_address = f"12 Demo Market Road, {DEMO_VENDOR_CITY}, {DEMO_VENDOR_STATE}"
    # Verified + enterprise => public listings, no transaction cap, no tier prompts.
    vendor.verification_status = "verified"
    vendor.verification_tier = "enterprise"
    vendor.verification_date = datetime.utcnow()
    vendor.commission_rate = 10.00
    vendor.specializations = ["Cement", "Blocks", "Roofing", "Tiles"]
    session.add(vendor)
    await session.commit()
    await session.refresh(vendor)
    return vendor


async def _ensure_products(session, vendor: Vendor) -> int:
    """Give the demo vendor a small active catalogue (idempotent, keyed by SKU).

    Without active products the broadcast matcher never selects this vendor, so it
    would receive no demand notification and no bid thread - only the city-filtered
    alert list would show anything.
    """
    categories = {
        name: cid
        for name, cid in (
            await session.execute(select(Category.name, Category.id))
        ).all()
    }
    seeded = 0
    for spec in DEMO_PRODUCTS:
        category_id = categories.get(spec["category"])
        if category_id is None:
            print(f"[skip] {spec['sku']}: category '{spec['category']}' not found")
            continue
        product = (
            await session.execute(select(Product).where(Product.sku == spec["sku"]))
        ).scalar_one_or_none()
        if product is None:
            product = Product(
                sku=spec["sku"],
                slug=spec["sku"].lower(),
                vendor_id=vendor.id,
            )
        product.vendor_id = vendor.id
        product.category_id = category_id
        product.name = spec["name"]
        product.description = spec["description"]
        product.short_description = spec["short_description"]
        product.meta_keywords = spec["meta_keywords"]
        product.base_price = spec["base_price"]
        product.quantity = spec["quantity"]
        product.unit_of_measure = spec["unit_of_measure"]
        product.minimum_order_quantity = 1
        product.status = ProductStatus.ACTIVE
        product.is_verified = True
        session.add(product)
        seeded += 1

    await session.commit()
    return seeded


async def _ensure_driver(session, user: User, phone: str) -> DriverProfile:
    profile = (
        await session.execute(select(DriverProfile).where(DriverProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        profile = DriverProfile(user_id=user.id)
    profile.vendor_id = None
    profile.source = "self"
    profile.status = "active"
    profile.full_name = "Dayo Driver"
    profile.phone = phone
    # Online + full photo set => /deliveries/offers actually returns jobs.
    profile.availability = True
    profile.vehicle_type = "Pickup Truck"
    profile.vehicle_plate = "DEMO-001"
    profile.vehicle_capacity = "1.5 tonnes"
    profile.license_no = "DEMO-DL-0001"
    profile.vehicle_photo_front = _PHOTO.format("Front")
    profile.vehicle_photo_back = _PHOTO.format("Back")
    profile.vehicle_photo_left = _PHOTO.format("Left")
    profile.vehicle_photo_right = _PHOTO.format("Right")
    profile.vehicle_photos_updated_at = datetime.utcnow()
    profile.default_address = "12 Demo Market Road, Ikeja, Lagos"
    profile.default_city = "Ikeja"
    profile.default_state = "Lagos"
    profile.default_lat = 6.5244
    profile.default_lng = 3.3792
    profile.current_lat = 6.5244
    profile.current_lng = 3.3792
    profile.current_location_updated_at = datetime.utcnow()
    profile.bank_name = "Demo Bank"
    profile.bank_account_number = "0123456789"
    profile.bank_account_name = "Dayo Driver"
    profile.bank_code = "000"
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def _ensure_tokens(session, user: User, tokens: int) -> TokenUsage:
    usage = (
        await session.execute(select(TokenUsage).where(TokenUsage.user_id == user.id))
    ).scalar_one_or_none()
    if usage is None:
        usage = TokenUsage(
            user_id=user.id,
            balance=0,
            lifetime_purchased=0,
            lifetime_consumed=0,
            free_tier_used_this_month=0,
            free_tier_month=datetime.utcnow().strftime("%Y-%m"),
        )
        session.add(usage)
        await session.flush()

    if usage.balance < tokens:
        delta = tokens - usage.balance
        usage.balance = tokens
        usage.lifetime_purchased += delta
        session.add(TokenTransaction(
            user_id=user.id,
            transaction_type="purchase",
            amount=delta,
            balance_after=usage.balance,
            description=f"Demo account grant: {tokens} tokens",
        ))
    await session.commit()
    await session.refresh(usage)
    return usage


async def seed(tokens: int, password_override: str | None, reset_password: bool) -> None:
    print(f"Target database: {_mask(str(engine.url))}")
    async with AsyncSessionLocal() as session:
        for spec in DEMO_USERS:
            label = spec["label"]
            role = spec["role"]
            password = password_override or DEMO_PASSWORD[label]
            user = await _upsert_user(session, spec, password, reset_password)

            extra = ""
            if role == "vendor":
                vendor = await _ensure_vendor(session, user)
                # Read the vendor columns BEFORE the catalogue commit expires them
                # (a lazy refresh inside this async block would raise MissingGreenlet).
                vendor_state = (f"{vendor.verification_status}/{vendor.verification_tier} "
                                f"city={vendor.city}")
                product_count = await _ensure_products(session, vendor)
                extra = f"vendor={vendor_state} products={product_count}"
            elif role == "driver":
                driver = await _ensure_driver(session, user, spec["phone_number"])
                extra = (f"driver={driver.status} online={driver.availability} "
                         f"photos_complete={driver.vehicle_photos_complete}")

            usage = await _ensure_tokens(session, user, tokens)
            print(f"[OK] {label:7} {user.email} id={str(user.id)[:8]} "
                  f"status={user.status} tokens={usage.balance} {extra}")

    print("\nDemo credentials (email / password):")
    for spec in DEMO_USERS:
        print(f"  {spec['label']:7}  {spec['email']:28}  "
              f"{password_override or DEMO_PASSWORD[spec['label']]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed builder/vendor/driver demo accounts.")
    parser.add_argument("--tokens", type=int, default=5000,
                        help="Token balance to ensure (default: 5000)")
    parser.add_argument("--password", help="Use one password for all three demo accounts")
    parser.add_argument("--reset", action="store_true",
                        help="Re-set the password on existing demo accounts")
    args = parser.parse_args()

    if args.password:
        for key in DEMO_PASSWORD:
            DEMO_PASSWORD[key] = args.password

    asyncio.run(seed(args.tokens, args.password, args.reset))


if __name__ == "__main__":
    main()
