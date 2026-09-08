"""add delivery_ratings table + driver rating aggregates (Phase 1B).

Additive migration only:
- creates the new `delivery_ratings` table and indexes, and
- adds two nullable/default aggregate columns to `driver_profiles`
  (`rating`, `rating_count`).

No columns/tables used by auth, onboarding, orders, payments or the delivery
lifecycle are dropped, renamed or type-changed.

Revision ID: 20260908_driver_ratings
Revises: 20260908_delivery_proofs
Create Date: 2026-09-08 00:00:00.000000

NOTE (manual application): apply in order after the Phase 1A migration, e.g.
`alembic upgrade 20260908_driver_ratings`. See the Phase 1A migration docstring
about the two-head history / `down_revision` repointing if your DB is on the
`0fb735a539b3` lineage.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260908_driver_ratings"
down_revision = "20260908_delivery_proofs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_ratings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("delivery_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("driver_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewer_name", sa.String(length=100), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_verified_purchase", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["delivery_job_id"], ["delivery_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["driver_profile_id"], ["driver_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delivery_ratings_driver_profile_id", "delivery_ratings", ["driver_profile_id"], unique=False)
    op.create_index("ix_delivery_ratings_delivery_job_id", "delivery_ratings", ["delivery_job_id"], unique=True)
    op.create_index("ix_delivery_ratings_user_id", "delivery_ratings", ["user_id"], unique=False)

    # Driver aggregate rating columns (additive).
    op.add_column("driver_profiles", sa.Column("rating", sa.Numeric(3, 2), nullable=True))
    op.add_column(
        "driver_profiles",
        sa.Column("rating_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("driver_profiles", "rating_count")
    op.drop_column("driver_profiles", "rating")
    op.drop_index("ix_delivery_ratings_user_id", table_name="delivery_ratings")
    op.drop_index("ix_delivery_ratings_delivery_job_id", table_name="delivery_ratings")
    op.drop_index("ix_delivery_ratings_driver_profile_id", table_name="delivery_ratings")
    op.drop_table("delivery_ratings")
