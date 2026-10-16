"""negotiation final price + product favourites

Additive migration only (no existing column/table is dropped or changed):

* `negotiations.agreed_price/agreed_at/agreed_by` — the two-party agreed
  absolute per-unit final price, set when the counterparty accepts a proposal.
  Checkout auto-applies it as the unit price for the agreeing builder only.
* `negotiation_counter_offers.final_price` — optional absolute per-unit price
  proposed alongside the existing discount percentage.
* `product_favorites` — per-user saved products; guests use localStorage and
  replay to the API on sign-in (mirrors the guest-cart flow).

Revision ID: 20261011_negotiation_final_price
Revises: 20261009_vendor_open_to_negotiate
Create Date: 2026-10-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261011_negotiation_final_price"
down_revision = "20261009_vendor_open_to_negotiate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── negotiations: agreed final price (all nullable/additive) ─────────────
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='negotiations' AND column_name='agreed_price'
            ) THEN
                ALTER TABLE negotiations ADD COLUMN agreed_price NUMERIC(15, 2);
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='negotiations' AND column_name='agreed_at'
            ) THEN
                ALTER TABLE negotiations ADD COLUMN agreed_at TIMESTAMP;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='negotiations' AND column_name='agreed_by'
            ) THEN
                ALTER TABLE negotiations ADD COLUMN agreed_by VARCHAR(20);
            END IF;
        END $$;
    """)

    # ── counter offers: optional absolute per-unit price ─────────────────────
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='negotiation_counter_offers' AND column_name='final_price'
            ) THEN
                ALTER TABLE negotiation_counter_offers ADD COLUMN final_price NUMERIC(15, 2);
            END IF;
        END $$;
    """)

    # ── product favourites ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_favorites (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT uq_product_favorites_user_product UNIQUE (user_id, product_id)
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_favorites_user_id ON product_favorites (user_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_favorites_product_id ON product_favorites (product_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_favorites_created_at ON product_favorites (created_at);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS product_favorites;")
    op.drop_column("negotiation_counter_offers", "final_price")
    op.drop_column("negotiations", "agreed_by")
    op.drop_column("negotiations", "agreed_at")
    op.drop_column("negotiations", "agreed_price")
