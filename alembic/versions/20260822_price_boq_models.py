"""add price anomaly + BOQ analysis tables.

Revision ID: 20260822_price_boq_models
Revises: 20260822_fraud_models
Create Date: 2026-08-22 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260822_price_boq_models"
down_revision = "20260822_fraud_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── price_anomalies ────────────────────────────────────────────
    op.create_table(
        "price_anomalies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("anomaly_number", sa.String(length=50), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_name", sa.String(length=500), nullable=False),
        sa.Column("supplier_name", sa.String(length=255), nullable=True),
        sa.Column("market_price", sa.Numeric(15, 2), nullable=True),
        sa.Column("quoted_price", sa.Numeric(15, 2), nullable=True),
        sa.Column("variance_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["vendors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_anomalies_anomaly_number", "price_anomalies", ["anomaly_number"], unique=True)
    op.create_index("ix_price_anomalies_detected_at", "price_anomalies", ["detected_at"])
    op.create_index("ix_price_anomalies_status", "price_anomalies", ["status"])

    # ── price_anomaly_history ──────────────────────────────────────
    op.create_table(
        "price_anomaly_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("anomaly_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("price", sa.Numeric(15, 2), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["anomaly_id"], ["price_anomalies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_anomaly_history_anomaly_id", "price_anomaly_history", ["anomaly_id"])

    # ── boq_analyses ───────────────────────────────────────────────
    op.create_table(
        "boq_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("boq_number", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("total_items", sa.Integer(), nullable=True),
        sa.Column("flagged_items", sa.Integer(), nullable=True),
        sa.Column("total_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("quoted_value", sa.Numeric(15, 2), nullable=True),
        sa.Column("potential_savings", sa.Numeric(15, 2), nullable=True),
        sa.Column("avg_variance", sa.Numeric(5, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_boq_analyses_boq_number", "boq_analyses", ["boq_number"], unique=True)
    op.create_index("ix_boq_analyses_created_at", "boq_analyses", ["created_at"])

    # ── boq_analysis_items ─────────────────────────────────────────
    op.create_table(
        "boq_analysis_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("boq_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("item_name", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 2), nullable=True),
        sa.Column("quoted_price", sa.Numeric(15, 2), nullable=True),
        sa.Column("market_price", sa.Numeric(15, 2), nullable=True),
        sa.Column("variance_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("potential_saving", sa.Numeric(15, 2), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["boq_id"], ["boq_analyses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_boq_analysis_items_boq_id", "boq_analysis_items", ["boq_id"])

    # ── boq_analysis_flags ─────────────────────────────────────────
    op.create_table(
        "boq_analysis_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("boq_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("issue", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["boq_id"], ["boq_analyses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["boq_analysis_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_boq_analysis_flags_boq_id", "boq_analysis_flags", ["boq_id"])


def downgrade() -> None:
    op.drop_index("ix_boq_analysis_flags_boq_id", table_name="boq_analysis_flags")
    op.drop_table("boq_analysis_flags")
    op.drop_index("ix_boq_analysis_items_boq_id", table_name="boq_analysis_items")
    op.drop_table("boq_analysis_items")
    op.drop_index("ix_boq_analyses_created_at", table_name="boq_analyses")
    op.drop_index("ix_boq_analyses_boq_number", table_name="boq_analyses")
    op.drop_table("boq_analyses")
    op.drop_index("ix_price_anomaly_history_anomaly_id", table_name="price_anomaly_history")
    op.drop_table("price_anomaly_history")
    op.drop_index("ix_price_anomalies_status", table_name="price_anomalies")
    op.drop_index("ix_price_anomalies_detected_at", table_name="price_anomalies")
    op.drop_index("ix_price_anomalies_anomaly_number", table_name="price_anomalies")
    op.drop_table("price_anomalies")