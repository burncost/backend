"""add vendor_reviews table for vendor-level ratings.

Revision ID: 20260816_vendor_reviews
Revises: 7416347e1cc7
Create Date: 2026-08-16 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260816_vendor_reviews"
down_revision = "7416347e1cc7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewer_name", sa.String(length=100), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_verified_purchase", sa.Boolean(), nullable=True),
        sa.Column("is_approved", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vendor_reviews_vendor_id", "vendor_reviews", ["vendor_id"])
    op.create_index("ix_vendor_reviews_user_id", "vendor_reviews", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_vendor_reviews_user_id", table_name="vendor_reviews")
    op.drop_index("ix_vendor_reviews_vendor_id", table_name="vendor_reviews")
    op.drop_table("vendor_reviews")