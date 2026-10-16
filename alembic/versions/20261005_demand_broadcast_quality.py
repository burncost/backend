"""demand broadcast + product quality.

Additive migration only: adds a nullable `quality` tier to `products` and, so a
BOQ-triggered demand broadcast can own a per-vendor bid thread, a nullable
`demand_alert_id` link plus a nullable `bid_price` on `negotiations`. No existing
column/table is dropped, renamed or type-changed, and older rows simply read back
NULLs.

Revision ID: 20261005_demand_broadcast_quality
Revises: 4d2648a38c3d
Create Date: 2026-10-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20261005_demand_broadcast_quality"
down_revision = "4d2648a38c3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("quality", sa.String(length=50), nullable=True))

    op.add_column(
        "negotiations",
        sa.Column("demand_alert_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "negotiations",
        sa.Column("bid_price", sa.Numeric(15, 2), nullable=True),
    )
    op.create_foreign_key(
        "fk_negotiations_demand_alert_id",
        "negotiations",
        "demand_alerts",
        ["demand_alert_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_negotiations_demand_alert_id", "negotiations", ["demand_alert_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_negotiations_demand_alert_id", table_name="negotiations")
    op.drop_constraint(
        "fk_negotiations_demand_alert_id", "negotiations", type_="foreignkey"
    )
    op.drop_column("negotiations", "bid_price")
    op.drop_column("negotiations", "demand_alert_id")
    op.drop_column("products", "quality")
