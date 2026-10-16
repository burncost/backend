"""orders.promo_code

Additive migration only: adds a nullable `promo_code` on `orders`. The promo
counter is incremented at order creation (not payment), so an abandoned draft
needs to know which code it consumed in order to release the use when it is
deleted, cancelled or expired. No existing column/table is dropped, renamed or
type-changed, and older rows simply read back NULL.

Revision ID: 20261006_orders_promo_code
Revises: 20261005_demand_broadcast_quality
Create Date: 2026-10-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261006_orders_promo_code"
down_revision = "20261005_demand_broadcast_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("promo_code", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "promo_code")
