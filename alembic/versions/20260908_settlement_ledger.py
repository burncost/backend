"""add vendor_settlement_ledger table (Phase 5 — Settlement & Disbursement).

Additive migration only: creates the new `vendor_settlement_ledger` table and
indexes. No columns/tables used by auth, onboarding, orders, payments or the
delivery lifecycle are dropped, renamed or type-changed. This table is NOT yet
written to by any live flow — it is the inert accounting foundation.

Revision ID: 20260908_settlement_ledger
Revises: 20260908_order_messages
Create Date: 2026-09-08 00:00:00.000000

NOTE (manual application): apply in order, e.g.
`alembic upgrade 20260908_settlement_ledger`. See the Phase 1A migration docstring
about the two-head history / `down_revision` repointing if needed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260908_settlement_ledger"
down_revision = "20260908_order_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_settlement_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("gross_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("gateway_fee", sa.Numeric(15, 2), nullable=False),
        sa.Column("commission_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("net_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("payout_reference", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vendor_settlement_ledger_vendor_id", "vendor_settlement_ledger", ["vendor_id"], unique=False)
    op.create_index("ix_vendor_settlement_ledger_order_id", "vendor_settlement_ledger", ["order_id"], unique=True)
    op.create_index("ix_vendor_settlement_ledger_period", "vendor_settlement_ledger", ["period"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_vendor_settlement_ledger_period", table_name="vendor_settlement_ledger")
    op.drop_index("ix_vendor_settlement_ledger_order_id", table_name="vendor_settlement_ledger")
    op.drop_index("ix_vendor_settlement_ledger_vendor_id", table_name="vendor_settlement_ledger")
    op.drop_table("vendor_settlement_ledger")
