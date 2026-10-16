"""add open_to_negotiate flag to vendors

Additive migration only: adds a boolean `open_to_negotiate` on `vendors` so a
vendor can opt in/out of price negotiation with buyers. Defaults to FALSE and
older rows read back FALSE. No existing column/table is dropped or changed.

Revision ID: 20261009_vendor_open_to_negotiate
Revises: 20261007_notifications_link
Create Date: 2026-10-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261009_vendor_open_to_negotiate"
down_revision = "20261007_notifications_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='vendors' AND column_name='open_to_negotiate'
            ) THEN
                ALTER TABLE vendors ADD COLUMN open_to_negotiate BOOLEAN NOT NULL DEFAULT FALSE;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_column("vendors", "open_to_negotiate")
