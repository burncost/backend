"""promo_codes.max_uses_per_user

Additive migration only: adds `max_uses_per_user` to `promo_codes` so a promo can
cap how many times a single buyer may redeem it, independently of the existing
global `max_uses` cap. Default 0 = unlimited per buyer, so existing codes keep
their current behaviour. No column/table is dropped, renamed or type-changed.

Revision ID: 20261016_promo_max_uses_per_user
Revises: 20261014_promo_discount_amount
Create Date: 2026-10-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261016_promo_max_uses_per_user"
down_revision = "20261014_promo_discount_amount"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "promo_codes",
        sa.Column("max_uses_per_user", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("promo_codes", "max_uses_per_user")
