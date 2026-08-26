"""add dispute tables.

Revision ID: 20260822_dispute_models
Revises: 20260822_price_boq_models
Create Date: 2026-08-22 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260822_dispute_models"
down_revision = "20260822_price_boq_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dispute_number", sa.String(length=50), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dispute_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("priority", sa.String(length=20), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("buyer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("buyer_name", sa.String(length=255), nullable=True),
        sa.Column("supplier_name", sa.String(length=255), nullable=True),
        sa.Column("order_number", sa.String(length=50), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("filed_by", sa.String(length=100), nullable=True),
        sa.Column("filed_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["supplier_id"], ["vendors.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_disputes_dispute_number", "disputes", ["dispute_number"], unique=True)
    op.create_index("ix_disputes_status", "disputes", ["status"])
    op.create_index("ix_disputes_filed_at", "disputes", ["filed_at"])

    op.create_table(
        "dispute_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dispute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_by", sa.String(length=20), nullable=True),
        sa.Column("evidence_type", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dispute_id"], ["disputes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dispute_evidence_dispute_id", "dispute_evidence", ["dispute_id"])

    op.create_table(
        "dispute_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dispute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resolution_type", sa.String(length=50), nullable=True),
        sa.Column("amount_refunded", sa.Numeric(15, 2), nullable=True),
        sa.Column("amount_released", sa.Numeric(15, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(length=100), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dispute_id"], ["disputes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dispute_resolutions_dispute_id", "dispute_resolutions", ["dispute_id"])

    op.create_table(
        "dispute_timeline",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dispute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dispute_id"], ["disputes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dispute_timeline_dispute_id", "dispute_timeline", ["dispute_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_timeline_dispute_id", table_name="dispute_timeline")
    op.drop_table("dispute_timeline")
    op.drop_index("ix_dispute_resolutions_dispute_id", table_name="dispute_resolutions")
    op.drop_table("dispute_resolutions")
    op.drop_index("ix_dispute_evidence_dispute_id", table_name="dispute_evidence")
    op.drop_table("dispute_evidence")
    op.drop_index("ix_disputes_filed_at", table_name="disputes")
    op.drop_index("ix_disputes_status", table_name="disputes")
    op.drop_index("ix_disputes_dispute_number", table_name="disputes")
    op.drop_table("disputes")