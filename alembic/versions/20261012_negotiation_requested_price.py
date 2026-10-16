"""negotiation requested (bid) price

Additive migration only (no existing column/table is dropped or changed):

* `negotiations.requested_price` - the buyer's opening absolute per-unit bid.
  Buyers now bid a naira figure and `requested_discount` is derived from the
  product's catalogue `base_price` server-side (so the buyer never does the
  maths). Additive + nullable: existing rows read back NULL and keep showing
  their percentage.

Revision ID: 20261012_negotiation_requested_price
Revises: 20261011_negotiation_final_price
Create Date: 2026-10-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261012_negotiation_requested_price"
down_revision = "20261011_negotiation_final_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── negotiations: the buyer's opening absolute per-unit bid ──────────────
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='negotiations' AND column_name='requested_price'
            ) THEN
                ALTER TABLE negotiations ADD COLUMN requested_price NUMERIC(15, 2);
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_column("negotiations", "requested_price")
