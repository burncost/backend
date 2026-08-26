"""add fraud alert tables.

Revision ID: 20260822_fraud_models
Revises: 20260822_negotiation_models
Create Date: 2026-08-22 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260822_fraud_models"
down_revision = "20260822_negotiation_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fraud_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("alert_number", sa.String(length=50), nullable=False),
        sa.Column("alert_type", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("is_negotiation", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fraud_alerts_alert_number", "fraud_alerts", ["alert_number"], unique=True)
    op.create_index("ix_fraud_alerts_detected_at", "fraud_alerts", ["detected_at"])
    op.create_index("ix_fraud_alerts_status_severity", "fraud_alerts", ["status", "severity"])

    op.create_table(
        "fraud_alert_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("account_id", sa.String(length=50), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("account_email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["alert_id"], ["fraud_alerts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fraud_alert_accounts_alert_id", "fraud_alert_accounts", ["alert_id"])

    op.create_table(
        "fraud_alert_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", sa.String(length=50), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["alert_id"], ["fraud_alerts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fraud_alert_transactions_alert_id", "fraud_alert_transactions", ["alert_id"])


def downgrade() -> None:
    op.drop_index("ix_fraud_alert_transactions_alert_id", table_name="fraud_alert_transactions")
    op.drop_table("fraud_alert_transactions")
    op.drop_index("ix_fraud_alert_accounts_alert_id", table_name="fraud_alert_accounts")
    op.drop_table("fraud_alert_accounts")
    op.drop_index("ix_fraud_alerts_status_severity", table_name="fraud_alerts")
    op.drop_index("ix_fraud_alerts_detected_at", table_name="fraud_alerts")
    op.drop_index("ix_fraud_alerts_alert_number", table_name="fraud_alerts")
    op.drop_table("fraud_alerts")