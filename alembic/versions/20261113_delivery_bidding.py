"""Post-confirmation delivery: driver bidding + separately-paid shipping.

Additive migration only - creates `shipping_invoices` and `delivery_bids`, then
adds the delivery-payment columns to `delivery_jobs`, `orders` and
`vendor_settlement_ledger`. No existing table/column is renamed or retyped.

Backfill choices:
  * `delivery_jobs.shipping_paid` defaults to TRUE, so every job dispatched before
    this feature keeps its current behaviour (drivers are never blocked).
  * `orders.shipping_payment_status` defaults to 'not_due' and
    `orders.delivery_fee_total` to 0, so historical orders report "nothing owed
    for delivery" without touching their stored totals.

Revision ID: 20261113_delivery_bidding
Revises: 20261029_newsletter_default_templates
Create Date: 2026-11-13 00:00:00.000000

NOTE (manual application): apply with
`alembic upgrade 20261113_delivery_bidding`.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20261113_delivery_bidding"
down_revision = "20261029_newsletter_default_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Delivery payment invoice (one row per payment attempt) ───────────────
    op.create_table(
        "shipping_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("provider_reference", sa.String(length=100), nullable=True),
        sa.Column("covered_job_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shipping_invoices_order_id", "shipping_invoices", ["order_id"], unique=False)
    op.create_index("ix_shipping_invoices_reference", "shipping_invoices", ["reference"], unique=True)
    op.create_index("ix_shipping_invoices_status", "shipping_invoices", ["status"], unique=False)

    # ── Driver bids on a `bid` delivery job ──────────────────────────────────
    op.create_table(
        "delivery_bids",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("driver_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["delivery_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["driver_profile_id"], ["driver_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "driver_profile_id", name="uq_delivery_bids_job_driver"),
    )
    op.create_index("ix_delivery_bids_job_id", "delivery_bids", ["job_id"], unique=False)
    op.create_index(
        "ix_delivery_bids_driver_profile_id", "delivery_bids", ["driver_profile_id"], unique=False
    )
    op.create_index("ix_delivery_bids_status", "delivery_bids", ["status"], unique=False)
    op.create_index("ix_delivery_bids_created_at", "delivery_bids", ["created_at"], unique=False)

    # ── Delivery jobs: bidding window + payment gate ─────────────────────────
    op.add_column("delivery_jobs", sa.Column("bidding_closes_at", sa.DateTime(), nullable=True))
    op.add_column(
        "delivery_jobs",
        sa.Column("shipping_paid", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "delivery_jobs",
        sa.Column("shipping_invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("delivery_jobs", sa.Column("vendor_fee_declined_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_delivery_jobs_bidding_closes_at", "delivery_jobs", ["bidding_closes_at"], unique=False
    )
    op.create_index("ix_delivery_jobs_shipping_paid", "delivery_jobs", ["shipping_paid"], unique=False)
    op.create_foreign_key(
        "fk_delivery_jobs_shipping_invoice_id",
        "delivery_jobs",
        "shipping_invoices",
        ["shipping_invoice_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── Orders: delivery-payment state (checkout total untouched) ────────────
    op.add_column(
        "orders",
        sa.Column(
            "shipping_payment_status", sa.String(length=20), nullable=False, server_default="not_due"
        ),
    )
    op.add_column("orders", sa.Column("shipping_paid_at", sa.DateTime(), nullable=True))
    op.add_column(
        "orders",
        sa.Column("delivery_fee_total", sa.Numeric(15, 2), nullable=True, server_default="0"),
    )
    op.create_index(
        "ix_orders_shipping_payment_status", "orders", ["shipping_payment_status"], unique=False
    )

    # ── Settlement ledger: vendor-fleet transport collected separately ───────
    op.add_column(
        "vendor_settlement_ledger",
        sa.Column("delivery_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("vendor_settlement_ledger", "delivery_amount")

    op.drop_index("ix_orders_shipping_payment_status", table_name="orders")
    op.drop_column("orders", "delivery_fee_total")
    op.drop_column("orders", "shipping_paid_at")
    op.drop_column("orders", "shipping_payment_status")

    op.drop_constraint(
        "fk_delivery_jobs_shipping_invoice_id", "delivery_jobs", type_="foreignkey"
    )
    op.drop_index("ix_delivery_jobs_shipping_paid", table_name="delivery_jobs")
    op.drop_index("ix_delivery_jobs_bidding_closes_at", table_name="delivery_jobs")
    op.drop_column("delivery_jobs", "vendor_fee_declined_at")
    op.drop_column("delivery_jobs", "shipping_invoice_id")
    op.drop_column("delivery_jobs", "shipping_paid")
    op.drop_column("delivery_jobs", "bidding_closes_at")

    op.drop_index("ix_delivery_bids_created_at", table_name="delivery_bids")
    op.drop_index("ix_delivery_bids_status", table_name="delivery_bids")
    op.drop_index("ix_delivery_bids_driver_profile_id", table_name="delivery_bids")
    op.drop_index("ix_delivery_bids_job_id", table_name="delivery_bids")
    op.drop_table("delivery_bids")

    op.drop_index("ix_shipping_invoices_status", table_name="shipping_invoices")
    op.drop_index("ix_shipping_invoices_reference", table_name="shipping_invoices")
    op.drop_index("ix_shipping_invoices_order_id", table_name="shipping_invoices")
    op.drop_table("shipping_invoices")

