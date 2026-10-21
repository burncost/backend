"""Demand agreements: per-item broadcast identity + per-transaction agreed prices

Additive and nullable throughout - no backfill, no rewrite of existing rows. The
old 48h demand window is simply frozen in application code (see
`demand_alert_cutoff`), so no data migration is needed for it either.

Revision ID: 20261024_demand_alert_agreements
Revises: 20261023_driver_vehicle_photos
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261024_demand_alert_agreements"
down_revision = "20261023_driver_vehicle_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Demand alerts: take-off identity, dedupe key and the agreement ────────
    op.add_column("demand_alerts", sa.Column("material_key", sa.String(100), nullable=True))
    op.add_column("demand_alerts", sa.Column("spec", sa.String(255), nullable=True))
    op.add_column("demand_alerts", sa.Column("source_ref", sa.String(255), nullable=True))
    op.add_column("demand_alerts", sa.Column("dedupe_key", sa.String(255), nullable=True))
    op.add_column(
        "demand_alerts",
        sa.Column(
            "agreed_vendor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vendors.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("demand_alerts", sa.Column("agreed_price", sa.Numeric(15, 2), nullable=True))
    op.add_column("demand_alerts", sa.Column("agreed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("demand_alerts", sa.Column("agreed_by", sa.String(20), nullable=True))
    op.create_index("ix_demand_alerts_material_key", "demand_alerts", ["material_key"])
    op.create_index("ix_demand_alerts_dedupe_key", "demand_alerts", ["dedupe_key"])
    op.create_index("ix_demand_alerts_agreed_vendor_id", "demand_alerts", ["agreed_vendor_id"])

    # ── Counter offers: catalogue product attached to a price proposal ────────
    op.add_column(
        "negotiation_counter_offers",
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "negotiation_counter_offers",
        sa.Column("product_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_negotiation_counter_offers_product_id",
        "negotiation_counter_offers",
        ["product_id"],
    )

    # ── Cart lines: the agreement that prices this line (single use) ──────────
    op.add_column(
        "cart_items",
        sa.Column(
            "negotiation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("negotiations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_cart_items_negotiation_id", "cart_items", ["negotiation_id"])

    # ── Negotiations: stamped once the agreement has priced an order ──────────
    op.add_column(
        "negotiations",
        sa.Column(
            "agreed_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_negotiations_agreed_order_id", "negotiations", ["agreed_order_id"])


def downgrade() -> None:
    op.drop_index("ix_negotiations_agreed_order_id", table_name="negotiations")
    op.drop_column("negotiations", "agreed_order_id")

    op.drop_index("ix_cart_items_negotiation_id", table_name="cart_items")
    op.drop_column("cart_items", "negotiation_id")

    op.drop_index(
        "ix_negotiation_counter_offers_product_id",
        table_name="negotiation_counter_offers",
    )
    op.drop_column("negotiation_counter_offers", "product_snapshot")
    op.drop_column("negotiation_counter_offers", "product_id")

    op.drop_index("ix_demand_alerts_agreed_vendor_id", table_name="demand_alerts")
    op.drop_index("ix_demand_alerts_dedupe_key", table_name="demand_alerts")
    op.drop_index("ix_demand_alerts_material_key", table_name="demand_alerts")
    op.drop_column("demand_alerts", "agreed_by")
    op.drop_column("demand_alerts", "agreed_at")
    op.drop_column("demand_alerts", "agreed_price")
    op.drop_column("demand_alerts", "agreed_vendor_id")
    op.drop_column("demand_alerts", "dedupe_key")
    op.drop_column("demand_alerts", "source_ref")
    op.drop_column("demand_alerts", "spec")
    op.drop_column("demand_alerts", "material_key")
