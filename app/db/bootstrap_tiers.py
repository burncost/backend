"""Idempotent bootstrap for vendor verification tiers (table + columns + seed)."""
import json
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

TIERS = [
    {
        "tier_code": "cac_only", "display_name": "CAC Verified", "sort_order": 1,
        "transaction_cap": 5_000_000, "commission_rate": 10.00,
        "required_document_types": [], "requires_manual_review": False,
        "perks": ["Start selling immediately", "No documents required", "₦5,000,000 transaction limit"],
    },
    {
        "tier_code": "documented", "display_name": "Documented Business", "sort_order": 2,
        "transaction_cap": 50_000_000, "commission_rate": 7.50,
        "required_document_types": ["cac_certificate", "tax_clearance", "business_license", "utility_bill"],
        "requires_manual_review": False,
        "perks": ["₦50,000,000 limit", "7.5% commission", "24–48h escrow release", "Verified badge"],
    },
    {
        "tier_code": "trusted", "display_name": "Trusted Supplier", "sort_order": 3,
        "transaction_cap": 500_000_000, "commission_rate": 5.00,
        "required_document_types": ["vat_certificate", "director_id", "trade_reference_1", "trade_reference_2", "address_proof"],
        "requires_manual_review": True,
        "perks": ["₦500,000,000 limit", "5% commission", "T+1 payouts", "Trusted badge", "Priority support"],
    },
]


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
    await conn.execute(text("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS verification_tier VARCHAR(20) NOT NULL DEFAULT 'cac_only'"))
    await conn.execute(text("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS transaction_volume NUMERIC(15,2) DEFAULT 0.00"))
    await conn.execute(text("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS tier VARCHAR(20) DEFAULT 'cac_only'"))
    await conn.execute(text("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS review_status VARCHAR(20) DEFAULT 'pending'"))
    await conn.execute(text("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS reviewed_by UUID REFERENCES users(id)"))
    await conn.execute(text("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP"))

    for t in TIERS:
        row = {**t,
               "required_document_types": json.dumps(t["required_document_types"]),
               "perks": json.dumps(t["perks"])}
        await conn.execute(text("""
            INSERT INTO vendor_verification_tiers
                (tier_code, display_name, sort_order, transaction_cap, commission_rate,
                 required_document_types, requires_manual_review, perks)
            VALUES
                (:tier_code, :display_name, :sort_order, :transaction_cap, :commission_rate,
                 CAST(:required_document_types AS jsonb), :requires_manual_review, CAST(:perks AS jsonb))
            ON CONFLICT (tier_code) DO NOTHING
        """), row)

    await conn.commit()
    logger.info("Vendor verification tiers bootstrap complete")