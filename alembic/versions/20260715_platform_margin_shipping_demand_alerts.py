"""15th July - Platform margin, shipping tables, demand alerts

Revision ID: c0dbb09b0a78
Revises: bde771659014
Create Date: 2026-07-15 19:35:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ENUM

# revision identifiers
revision = 'c0dbb09b0a78'
down_revision = 'bde771659014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─────────────────────────────────────────────
    # 1. platform_margin on categories
    # ─────────────────────────────────────────────
    op.execute("ALTER TABLE categories ADD COLUMN IF NOT EXISTS platform_margin DECIMAL(5,2) DEFAULT 5.00")

    # ─────────────────────────────────────────────
    # 2. demand_alerts table
    # ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS demand_alerts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            item_description VARCHAR(500) NOT NULL,
            city VARCHAR(100) NOT NULL,
            quantity_needed DECIMAL(15,2),
            unit VARCHAR(50),
            project_title VARCHAR(500),
            requested_by UUID REFERENCES users(id),
            status VARCHAR(50) DEFAULT 'pending',
            notified_vendors TEXT[],
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_demand_alerts_city ON demand_alerts(city)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_demand_alerts_status ON demand_alerts(status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_demand_alerts_requested_by ON demand_alerts(requested_by)")

    # ─────────────────────────────────────────────
    # 3. shipping_zones table
    # ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS shipping_zones (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL,
            code VARCHAR(20) UNIQUE NOT NULL,
            base_rate DECIMAL(15,2) NOT NULL,
            rate_per_kg DECIMAL(10,2) DEFAULT 0,
            free_weight_kg DECIMAL(10,2) DEFAULT 10,
            handling_fee DECIMAL(15,2) DEFAULT 0,
            estimated_days_min INTEGER DEFAULT 1,
            estimated_days_max INTEGER DEFAULT 3,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # ─────────────────────────────────────────────
    # 4. shipping_zone_mappings table
    # ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS shipping_zone_mappings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            origin_state VARCHAR(100) NOT NULL,
            origin_city VARCHAR(100),
            destination_state VARCHAR(100) NOT NULL,
            destination_city VARCHAR(100),
            zone_id UUID NOT NULL REFERENCES shipping_zones(id),
            UNIQUE(origin_state, origin_city, destination_state, destination_city)
        )
    """)

    # ─────────────────────────────────────────────
    # 5. vendor_shipping_overrides table
    # ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS vendor_shipping_overrides (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
            zone_id UUID NOT NULL REFERENCES shipping_zones(id),
            custom_base_rate DECIMAL(15,2),
            custom_rate_per_kg DECIMAL(10,2),
            free_shipping_threshold DECIMAL(15,2),
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(vendor_id, zone_id)
        )
    """)

    # ─────────────────────────────────────────────
    # 6. Seed shipping zones
    # ─────────────────────────────────────────────
    op.execute("""
        INSERT INTO shipping_zones (name, code, base_rate, rate_per_kg, free_weight_kg, handling_fee, estimated_days_min, estimated_days_max)
        VALUES
            ('Local Delivery',    'local',     2500,  50,  20,  500,  1, 2),
            ('Metro Delivery',    'metro',     5000,  75,  15,  750,  1, 3),
            ('Regional Delivery', 'regional',  8000, 100,  10, 1000,  2, 5),
            ('National Delivery', 'national', 15000, 150,   5, 1500,  3, 7)
        ON CONFLICT (code) DO NOTHING
    """)

    # ─────────────────────────────────────────────
    # 7. Seed category margins
    # ─────────────────────────────────────────────
    op.execute("UPDATE categories SET platform_margin = 3.00  WHERE slug = 'cement'              AND platform_margin = 5.00")
    op.execute("UPDATE categories SET platform_margin = 5.00  WHERE slug = 'steel-iron'          AND platform_margin = 5.00")
    op.execute("UPDATE categories SET platform_margin = 7.00  WHERE slug = 'blocks-bricks'       AND platform_margin = 5.00")
    op.execute("UPDATE categories SET platform_margin = 8.00  WHERE slug = 'roofing-materials'   AND platform_margin = 5.00")
    op.execute("UPDATE categories SET platform_margin = 10.00 WHERE slug = 'plumbing-supplies'   AND platform_margin = 5.00")
    op.execute("UPDATE categories SET platform_margin = 10.00 WHERE slug = 'electrical-supplies' AND platform_margin = 5.00")
    op.execute("UPDATE categories SET platform_margin = 12.00 WHERE slug = 'paint-coating'       AND platform_margin = 5.00")
    op.execute("UPDATE categories SET platform_margin = 8.00  WHERE slug = 'wood-timber'         AND platform_margin = 5.00")
    op.execute("UPDATE categories SET platform_margin = 5.00  WHERE slug = 'sand-gravel'         AND platform_margin = 5.00")
    op.execute("UPDATE categories SET platform_margin = 15.00 WHERE slug = 'hardware-tools'      AND platform_margin = 5.00")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vendor_shipping_overrides")
    op.execute("DROP TABLE IF EXISTS shipping_zone_mappings")
    op.execute("DROP TABLE IF EXISTS shipping_zones")
    op.execute("DROP TABLE IF EXISTS demand_alerts")
    op.execute("ALTER TABLE categories DROP COLUMN IF EXISTS platform_margin")