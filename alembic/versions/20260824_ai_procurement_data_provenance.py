"""add ai_agent_logs, quotations, and token_usage chat columns.

Revision ID: 20260824_ai_procurement_data_provenance
Revises: 20260822_system_settings
Create Date: 2026-08-24 12:40:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260824_ai_procurement_data_provenance"
down_revision = "20260822_system_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── token_usage column additions ────────────────────────────────
    op.add_column("token_usage", sa.Column("chat_messages_used_this_month", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("token_usage", sa.Column("chat_messages_month", sa.String(length=7), nullable=True))

    # ── ai_agent_logs ───────────────────────────────────────────────
    op.create_table(
        "ai_agent_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", sa.String(length=100), nullable=True),
        sa.Column("intent", sa.String(length=100), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=True),
        sa.Column("tool_args", sa.JSON(), nullable=True),
        sa.Column("execution_status", sa.String(length=20), nullable=True),
        sa.Column("result_summary", sa.String(length=1000), nullable=True),
        sa.Column("execution_result", sa.JSON(), nullable=True),
        sa.Column("price_source", sa.String(length=20), nullable=True),
        sa.Column("quantity_source", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("fallback_used", sa.String(length=50), nullable=True),
        sa.Column("estimated_items", sa.Integer(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_agent_logs_user_id", "ai_agent_logs", ["user_id"])
    op.create_index("ix_ai_agent_logs_conversation_id", "ai_agent_logs", ["conversation_id"])
    op.create_index("ix_ai_agent_logs_intent", "ai_agent_logs", ["intent"])
    op.create_index("ix_ai_agent_logs_tool_name", "ai_agent_logs", ["tool_name"])
    op.create_index("ix_ai_agent_logs_created_at", "ai_agent_logs", ["created_at"])
    op.create_index("ix_ai_agent_logs_user_created", "ai_agent_logs", ["user_id", "created_at"])

    # ── quotations ──────────────────────────────────────────────────
    op.create_table(
        "quotations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("quotation_number", sa.String(length=50), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("supplier_name", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("total_quoted", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_market", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_overcharge", sa.Numeric(15, 2), nullable=True),
        sa.Column("inflated_count", sa.Integer(), nullable=True),
        sa.Column("fair_count", sa.Integer(), nullable=True),
        sa.Column("unverified_count", sa.Integer(), nullable=True),
        sa.Column("price_source", sa.String(length=20), nullable=True),
        sa.Column("demand_alerts_created", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quotations_quotation_number", "quotations", ["quotation_number"], unique=True)
    op.create_index("ix_quotations_user_id", "quotations", ["user_id"])
    op.create_index("ix_quotations_city", "quotations", ["city"])
    op.create_index("ix_quotations_created_at", "quotations", ["created_at"])
    op.create_index("ix_quotations_user_created", "quotations", ["user_id", "created_at"])

    # ── quotation_line_items ────────────────────────────────────────
    op.create_table(
        "quotation_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("quotation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 2), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("quoted_rate", sa.Numeric(15, 2), nullable=True),
        sa.Column("quoted_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("market_rate", sa.Numeric(15, 2), nullable=True),
        sa.Column("price_source", sa.String(length=20), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("deviation_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["quotation_id"], ["quotations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quotation_line_items_qid", "quotation_line_items", ["quotation_id"])


def downgrade() -> None:
    op.drop_index("ix_quotation_line_items_qid", table_name="quotation_line_items")
    op.drop_table("quotation_line_items")

    op.drop_index("ix_quotations_user_created", table_name="quotations")
    op.drop_index("ix_quotations_created_at", table_name="quotations")
    op.drop_index("ix_quotations_city", table_name="quotations")
    op.drop_index("ix_quotations_user_id", table_name="quotations")
    op.drop_index("ix_quotations_quotation_number", table_name="quotations")
    op.drop_table("quotations")

    op.drop_index("ix_ai_agent_logs_user_created", table_name="ai_agent_logs")
    op.drop_index("ix_ai_agent_logs_created_at", table_name="ai_agent_logs")
    op.drop_index("ix_ai_agent_logs_tool_name", table_name="ai_agent_logs")
    op.drop_index("ix_ai_agent_logs_intent", table_name="ai_agent_logs")
    op.drop_index("ix_ai_agent_logs_conversation_id", table_name="ai_agent_logs")
    op.drop_index("ix_ai_agent_logs_user_id", table_name="ai_agent_logs")
    op.drop_table("ai_agent_logs")

    op.drop_column("token_usage", "chat_messages_month")
