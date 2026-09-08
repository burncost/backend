"""add payment_captures table (Phase 5 — gateway capture persistence).

Additive migration only. Creates the new `payment_captures` table and indexes; no
existing table is dropped/renamed/type-changed. This table is not yet written by
any live flow (recording is flag-gated).

Revision ID: 20260908_payment_captures
Revises: 20260908_settlement_ledger
Create Date: 2026-09-08 00:00:00.000000

NOTE (manual application): apply in order, e.g.
`alembic upgrade 20260908_payment_captures`. See the Phase 1A migration docstring
about the two-head history / `down_revision` repointing if needed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260908_payment_captures"
down_revision = "20260908_settlement_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_captures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gateway", sa.String(length=20), nullable=False),
        sa.Column("provider_reference", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("gateway_fee", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_captures_order_id", "payment_captures", ["order_id"], unique=True)
    op.create_index("ix_payment_captures_gateway", "payment_captures", ["gateway"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_payment_captures_gateway", table_name="payment_captures")
    op.drop_index("ix_payment_captures_order_id", table_name="payment_captures")
    op.drop_table("payment_captures")
