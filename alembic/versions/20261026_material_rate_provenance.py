"""Material rate provenance: cached online prices must not launder as verified

Adds the provenance columns the online write-back path needs, plus a natural
`lookup_key` so a re-observed online price updates its cached row instead of
duplicating it.

Everything is nullable / defaulted, so existing rows are untouched and keep
`is_provisional = false` (they were curated or manually entered).

Revision ID: 20261026_material_rate_provenance
Revises: 20261025_dispute_escrow_clock
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "20261026_material_rate_provenance"
down_revision = "20261025_dispute_escrow_clock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("material_rates", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("material_rates", sa.Column("confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column(
        "material_rates",
        sa.Column(
            "is_provisional",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("material_rates", sa.Column("observed_at", sa.DateTime(), nullable=True))
    op.add_column("material_rates", sa.Column("lookup_key", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_material_rates_is_provisional", "material_rates", ["is_provisional"]
    )
    op.create_index(
        "ix_material_rates_lookup_key", "material_rates", ["lookup_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_material_rates_lookup_key", table_name="material_rates")
    op.drop_index("ix_material_rates_is_provisional", table_name="material_rates")
    op.drop_column("material_rates", "lookup_key")
    op.drop_column("material_rates", "observed_at")
    op.drop_column("material_rates", "is_provisional")
    op.drop_column("material_rates", "confidence")
    op.drop_column("material_rates", "source_url")
