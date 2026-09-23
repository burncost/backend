"""Idempotent bootstrap for vendor verification tiers (table + columns + seed)."""
import json
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

TIERS = [
    {
        "tier_code": "starter", "display_name": "Starter", "sort_order": 1,
        "transaction_cap": 3_000_000,  # "commission_rate": 10.00,  # Phase 13: commission removed
        "required_document_types": [], "requires_manual_review": False,
        "perks": ["Start selling immediately after signup", "₦3,000,000 transaction limit"],
    },
    {
        "tier_code": "verified_vendor", "display_name": "Verified Vendor", "sort_order": 2,
        "transaction_cap": 10_000_000,  # "commission_rate": 7.50,  # Phase 13: commission removed
        "required_document_types": ["nin"], "requires_manual_review": False,
        "perks": ["₦10,000,000 transaction limit", "24–48h escrow release", "NIN verification only"],
    },
    {
        "tier_code": "enterprise", "display_name": "Enterprise", "sort_order": 3,
        # Effectively uncapped — UI shows "no cap" for enterprise.
        "transaction_cap": 999_999_999_999,  # "commission_rate": 5.00,  # Phase 13: commission removed
        "required_document_types": ["cac"], "requires_manual_review": False,
        "perks": ["No transaction cap", "T+1 payouts", "BurnCost Verified badge", "Priority support"],
    },
]

# Legacy tier codes -> new codes (bypass, not delete).
_TIER_BACKFILL = """
    UPDATE vendors SET verification_tier = CASE
        WHEN verification_tier IN ('tier_0', 'tier_1', 'cac_only') THEN 'starter'
        WHEN verification_tier IN ('tier_2', 'documented') THEN 'verified_vendor'
        WHEN verification_tier IN ('tier_3', 'trusted') THEN 'enterprise'
        ELSE 'starter'
    END
    WHERE verification_tier NOT IN ('starter', 'verified_vendor', 'enterprise');
"""


async def bootstrap_tiers(conn) -> None:
    """Create table + columns and seed rows. Pass an async SQLAlchemy connection."""
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS vendor_verification_tiers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tier_code VARCHAR(20) UNIQUE NOT NULL,
            display_name VARCHAR(50) NOT NULL,
            sort_order INTEGER DEFAULT 1,
            transaction_cap NUMERIC(16,2) NOT NULL DEFAULT 5000000,
            commission_rate NUMERIC(5,2) NOT NULL DEFAULT 10.00,
            required_document_types JSONB DEFAULT '[]'::jsonb,
            requires_manual_review BOOLEAN DEFAULT FALSE,
            perks JSONB DEFAULT '[]'::jsonb,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await conn.execute(text("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS verification_tier VARCHAR(20) NOT NULL DEFAULT 'starter'"))
    await conn.execute(text("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS transaction_volume NUMERIC(15,2) DEFAULT 0.00"))
    await conn.execute(text("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS tier VARCHAR(20) DEFAULT 'starter'"))
    await conn.execute(text("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS review_status VARCHAR(20) DEFAULT 'pending'"))
    await conn.execute(text("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS reviewed_by UUID REFERENCES users(id)"))
    await conn.execute(text("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP"))

    # Backfill legacy tier codes onto the new 3-tier scheme (bypass, not delete).
    await conn.execute(text(_TIER_BACKFILL))

    # Identity verification cache + cross-device upgrade drafts.
    await conn.execute(text("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS nin_verification_data JSONB"))
    await conn.execute(text("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS notification_preferences JSONB"))
    await conn.execute(text("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS cac_verification_data JSONB"))
    await conn.execute(text("ALTER TABLE vendor_drafts ADD COLUMN IF NOT EXISTS upgrade_data JSONB"))

    # Enforce NIN/CAC uniqueness at the DB level (partial: non-empty values only).
    await conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_vendors_nin
        ON vendors (nin) WHERE nin IS NOT NULL AND TRIM(nin) <> ''
    """))
    await conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_vendors_cac_number
        ON vendors (cac_business_registration_number)
        WHERE cac_business_registration_number IS NOT NULL AND TRIM(cac_business_registration_number) <> ''
    """))

    for t in TIERS:
        row = {**t,
               "required_document_types": json.dumps(t["required_document_types"]),
               "perks": json.dumps(t["perks"])}
        await conn.execute(text("""
            INSERT INTO vendor_verification_tiers
                (tier_code, display_name, sort_order, transaction_cap,
                 required_document_types, requires_manual_review, perks)
            VALUES
                (:tier_code, :display_name, :sort_order, :transaction_cap,
                 CAST(:required_document_types AS jsonb), :requires_manual_review, CAST(:perks AS jsonb))
            ON CONFLICT (tier_code) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                sort_order = EXCLUDED.sort_order,
                transaction_cap = EXCLUDED.transaction_cap,
                required_document_types = EXCLUDED.required_document_types,
                requires_manual_review = EXCLUDED.requires_manual_review,
                perks = EXCLUDED.perks
        """), row)

    await conn.commit()
    logger.info("Vendor verification tiers bootstrap complete")