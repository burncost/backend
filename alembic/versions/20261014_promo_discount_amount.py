"""promo_codes.discount_amount

Switch the promo discount from a percentage to a flat Naira amount. Renames
`promo_codes.discount_percent` (Numeric(5,2), a 0-100 percentage) to
`promo_codes.discount_amount` (Numeric(15,2), a whole-Naira amount such as 1000)
and widens the precision so large amounts fit. Existing values are preserved
as-is - a legacy row that stored `10` (meaning 10%) now reads as ₦10 off.

Revision ID: 20261014_promo_discount_amount
Revises: 20261013_promo_scope
Create Date: 2026-10-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261014_promo_discount_amount"
down_revision = "20261013_promo_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "promo_codes",
        "discount_percent",
        new_column_name="discount_amount",
        existing_type=sa.Numeric(5, 2),
        type_=sa.Numeric(15, 2),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "promo_codes",
        "discount_amount",
        new_column_name="discount_percent",
        existing_type=sa.Numeric(15, 2),
        type_=sa.Numeric(5, 2),
        existing_nullable=False,
    )
