"""add negotiation, discount config, and audit tables.

Revision ID: 20260822_negotiation_models
Revises: 20260816_vendor_reviews
Create Date: 2026-08-22 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260822_negotiation_models"
down_revision = "20260816_vendor_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── negotiations ───────────────────────────────────────────────
    op.create_table(
        "negotiations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("negotiation_number", sa.String(length=50), nullable=False),
        sa.Column("builder_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("product_name", sa.String(length=500), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("quantity", sa.Numeric(15, 2), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("requested_discount", sa.Numeric(5, 2), nullable=False),
        sa.Column("counter_offer", sa.Numeric(5, 2), nullable=True),
        sa.Column("final_discount", sa.Numeric(5, 2), nullable=True),
        sa.Column("value", sa.Numeric(15, 2), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("flagged", sa.Boolean(), nullable=True),
        sa.Column("suspended", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["builder_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_negotiations_negotiation_number", "negotiations", ["negotiation_number"], unique=True)
    op.create_index("ix_negotiations_builder_id", "negotiations", ["builder_id"])
    op.create_index("ix_negotiations_supplier_id", "negotiations", ["supplier_id"])
    op.create_index("ix_negotiations_status", "negotiations", ["status"])
    op.create_index("ix_negotiations_created_at", "negotiations", ["created_at"])
    op.create_index("ix_negotiations_status_created", "negotiations", ["status", "created_at"])

    # ── negotiation_counter_offers ─────────────────────────────────
    op.create_table(
        "negotiation_counter_offers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("negotiation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offered_by", sa.String(length=20), nullable=False),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["negotiation_id"], ["negotiations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_negotiation_counter_offers_negotiation_id", "negotiation_counter_offers", ["negotiation_id"])

    # ── discount_configurations ────────────────────────────────────
    op.create_table(
        "discount_configurations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("config_number", sa.String(length=50), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("product_name", sa.String(length=500), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("discount_enabled", sa.Boolean(), nullable=True),
        sa.Column("max_discount_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("auto_approval_threshold", sa.Numeric(5, 2), nullable=True),
        sa.Column("auto_rejection_threshold", sa.Numeric(5, 2), nullable=True),
        sa.Column("min_order_qty", sa.Integer(), nullable=True),
        sa.Column("min_order_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("quote_expiration_hours", sa.Integer(), nullable=True),
        sa.Column("last_modified_by", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["supplier_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_discount_configurations_config_number", "discount_configurations", ["config_number"], unique=True)
    op.create_index("ix_discount_configurations_supplier_id", "discount_configurations", ["supplier_id"])

    # ── negotiation_audit_entries ──────────────────────────────────
    op.create_table(
        "negotiation_audit_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("negotiation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("performed_by", sa.String(length=100), nullable=True),
        sa.Column("prev_value", sa.String(length=255), nullable=True),
        sa.Column("new_value", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["negotiation_id"], ["negotiations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_negotiation_audit_entries_negotiation_id", "negotiation_audit_entries", ["negotiation_id"])
    op.create_index("ix_negotiation_audit_entries_created_at", "negotiation_audit_entries", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_negotiation_audit_entries_created_at", table_name="negotiation_audit_entries")
    op.drop_index("ix_negotiation_audit_entries_negotiation_id", table_name="negotiation_audit_entries")
    op.drop_table("negotiation_audit_entries")

    op.drop_index("ix_discount_configurations_supplier_id", table_name="discount_configurations")
    op.drop_index("ix_discount_configurations_config_number", table_name="discount_configurations")
    op.drop_table("discount_configurations")

    op.drop_index("ix_negotiation_counter_offers_negotiation_id", table_name="negotiation_counter_offers")
    op.drop_table("negotiation_counter_offers")

    op.drop_index("ix_negotiations_status_created", table_name="negotiations")
    op.drop_index("ix_negotiations_created_at", table_name="negotiations")
    op.drop_index("ix_negotiations_status", table_name="negotiations")
    op.drop_index("ix_negotiations_supplier_id", table_name="negotiations")
    op.drop_index("ix_negotiations_builder_id", table_name="negotiations")
    op.drop_index("ix_negotiations_negotiation_number", table_name="negotiations")
    op.drop_table("negotiations")