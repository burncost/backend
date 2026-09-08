"""add user_email_preferences table (Phase 3 — email opt-in).

Additive migration only: creates the new `user_email_preferences` table and
indexes. No columns/tables used by auth, onboarding, orders or payments are
dropped, renamed or type-changed.

Revision ID: 20260908_email_preferences
Revises: 20260908_driver_earnings
Create Date: 2026-09-08 00:00:00.000000

NOTE (manual application): apply in order after the Phase 1 migrations, e.g.
`alembic upgrade 20260908_email_preferences`. See the Phase 1A migration docstring
about the two-head history / `down_revision` repointing if needed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260908_email_preferences"
down_revision = "20260908_driver_earnings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_email_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("marketing_opt_in", sa.Boolean(), nullable=False),
        sa.Column("opted_in_at", sa.DateTime(), nullable=True),
        sa.Column("opted_out_at", sa.DateTime(), nullable=True),
        sa.Column("unsubscribe_token", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_email_preferences_user_id", "user_email_preferences", ["user_id"], unique=True)
    op.create_index("ix_user_email_preferences_email", "user_email_preferences", ["email"], unique=False)
    op.create_index("ix_user_email_preferences_unsubscribe_token", "user_email_preferences", ["unsubscribe_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_user_email_preferences_unsubscribe_token", table_name="user_email_preferences")
    op.drop_index("ix_user_email_preferences_email", table_name="user_email_preferences")
    op.drop_index("ix_user_email_preferences_user_id", table_name="user_email_preferences")
    op.drop_table("user_email_preferences")
