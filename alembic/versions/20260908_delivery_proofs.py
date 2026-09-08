"""add delivery_proofs table (Proof of Delivery, Phase 1A).

Additive migration only: creates the new `delivery_proofs` table and indexes.
No columns/tables used by auth, onboarding, orders, payments or driver/order
lifecycle are dropped, renamed or type-changed.

Revision ID: 20260908_delivery_proofs
Revises: 20260908_vendor_tier_normalize
Create Date: 2026-09-08 00:00:00.000000

NOTE (manual application): the migration tree currently has two live heads
(`20260908_vendor_tier_normalize` and `0fb735a539b3`). This revision attaches to
`20260908_vendor_tier_normalize`. If your database's `alembic_version` row points
at `0fb735a539b3` instead, change `down_revision` below to '0fb735a539b3' (or run a
merge revision) before applying. Applying `alembic upgrade head` will not work while
two heads exist, so apply this by its specific revision id, e.g.
`alembic upgrade 20260908_delivery_proofs`.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260908_delivery_proofs"
down_revision = "20260908_vendor_tier_normalize"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_proofs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("delivery_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pod_type", sa.String(length=20), nullable=False),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("captured_lat", sa.Numeric(11, 8), nullable=True),
        sa.Column("captured_lng", sa.Numeric(11, 8), nullable=True),
        sa.Column("submitted_by", sa.String(length=20), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["delivery_job_id"], ["delivery_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delivery_proofs_delivery_job_id", "delivery_proofs", ["delivery_job_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_delivery_proofs_delivery_job_id", table_name="delivery_proofs")
    op.drop_table("delivery_proofs")
