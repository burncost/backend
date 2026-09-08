"""normalize vendors to data-driven tier codes

Map existing vendors onto the Tier 0-3 scheme derived from their data:
- CAC present -> tier_3 (verified)
- otherwise NIN present -> tier_2
- otherwise -> tier_1 (business exists; bio tier for fresh is set by the app)

Revision ID: 20260908_vendor_tier_normalize
Revises: 20260908_vendor_nin_rename
Create Date: 2026-09-08 13:00:00.000000
"""
from alembic import op

revision = "20260908_vendor_tier_normalize"
down_revision = "20260908_vendor_nin_rename"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE vendors
        SET verification_tier = CASE
            WHEN TRIM(COALESCE(cac_business_registration_number, '')) <> '' THEN 'tier_3'
            WHEN TRIM(COALESCE(nin, '')) <> '' THEN 'tier_2'
            ELSE 'tier_1'
        END,
        verification_status = CASE
            WHEN TRIM(COALESCE(cac_business_registration_number, '')) <> '' THEN 'verified'
            ELSE 'pending'
        END
        """
    )


def downgrade() -> None:
    # No-op data downgrade is intentionally skipped (lossy, monotonic tiers).
    pass