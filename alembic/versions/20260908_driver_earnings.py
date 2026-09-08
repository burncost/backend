"""add driver_earnings ledger (Phase 1C).

Additive migration only: creates the new `driver_earnings` table and indexes.
No columns/tables used by auth, onboarding, orders, payments or the delivery
lifecycle are dropped, renamed or type-changed.

Revision ID: 20260908_driver_earnings
Revises: 20260908_driver_ratings
Create Date: 2026-09-08 00:00:00.000000

NOTE (manual application): apply in order after the Phase 1B migration, e.g.
`alembic upgrade 20260908_driver_earnings`. See the Phase 1A migration docstring
about the two-head history / `down_revision` repointing if needed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260908_driver_earnings"
down_revision = "20260908_driver_ratings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "driver_earnings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("driver_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gross_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("commission_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("net_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("payout_reference", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["driver_profile_id"], ["driver_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["delivery_job_id"], ["delivery_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_driver_earnings_driver_profile_id", "driver_earnings", ["driver_profile_id"], unique=False)
    op.create_index("ix_driver_earnings_delivery_job_id", "driver_earnings", ["delivery_job_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_driver_earnings_delivery_job_id", table_name="driver_earnings")
    op.drop_index("ix_driver_earnings_driver_profile_id", table_name="driver_earnings")
    op.drop_table("driver_earnings")
