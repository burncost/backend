"""Dispute resolution spine: escrow hold, inspection window, refund records

Additive and nullable throughout - no backfill, no rewrite of existing rows.
Existing orders simply have `auto_release_at = NULL`, which the auto-release
task treats as "not scheduled" (it backfills from `delivered_at` only for rows
that were delivered but never stamped).

Revision ID: 20261025_dispute_escrow_clock
Revises: 20261024_demand_alert_agreements
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261025_dispute_escrow_clock"
down_revision = "20261024_demand_alert_agreements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Orders: the inspection clock the policy's auto-release depends on ────
    op.add_column(
        "orders",
        sa.Column("inspection_window_hours", sa.Integer(), nullable=True, server_default="48"),
    )
    op.add_column("orders", sa.Column("auto_release_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("escrow_released_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("inspection_extension_requested_at", sa.DateTime(), nullable=True))
    op.create_index("ix_orders_auto_release_at", "orders", ["auto_release_at"])

    # ── Disputes: the release guard filters by (order_id, status) ────────────
    op.create_index("ix_disputes_order_status", "disputes", ["order_id", "status"])

    # ── Refunds: the payout side of a resolution (recorded, paid by admin) ───
    op.create_table(
        "refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dispute_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("disputes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("gateway", sa.String(30), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(100), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("processed_by", sa.String(100), nullable=True),
        sa.Column("payout_reference", sa.String(100), nullable=True),
    )
    op.create_index("ix_refunds_order_id", "refunds", ["order_id"])
    op.create_index("ix_refunds_dispute_id", "refunds", ["dispute_id"])
    op.create_index("ix_refunds_status", "refunds", ["status"])
    op.create_index("ix_refunds_requested_at", "refunds", ["requested_at"])


def downgrade() -> None:
    op.drop_index("ix_refunds_requested_at", table_name="refunds")
    op.drop_index("ix_refunds_status", table_name="refunds")
    op.drop_index("ix_refunds_dispute_id", table_name="refunds")
    op.drop_index("ix_refunds_order_id", table_name="refunds")
    op.drop_table("refunds")

    op.drop_index("ix_disputes_order_status", table_name="disputes")

    op.drop_index("ix_orders_auto_release_at", table_name="orders")
    op.drop_column("orders", "inspection_extension_requested_at")
    op.drop_column("orders", "escrow_released_at")
    op.drop_column("orders", "auto_release_at")
    op.drop_column("orders", "inspection_window_hours")
