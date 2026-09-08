"""add order_messages table (Phase 4B — buyer↔vendor messaging).

Additive migration only: creates the new `order_messages` table and indexes. No
columns/tables used by auth, onboarding, orders, payments or the delivery
lifecycle are dropped, renamed or type-changed.

Revision ID: 20260908_order_messages
Revises: 20260908_email_preferences
Create Date: 2026-09-08 00:00:00.000000

NOTE (manual application): apply in order, e.g.
`alembic upgrade 20260908_order_messages`. See the Phase 1A migration docstring
about the two-head history / `down_revision` repointing if needed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260908_order_messages"
down_revision = "20260908_email_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sender_role", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_messages_order_id", "order_messages", ["order_id"], unique=False)
    op.create_index("ix_order_messages_sender_user_id", "order_messages", ["sender_user_id"], unique=False)
    op.create_index("ix_order_messages_read", "order_messages", ["read"], unique=False)
    op.create_index("ix_order_messages_created_at", "order_messages", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_order_messages_created_at", table_name="order_messages")
    op.drop_index("ix_order_messages_read", table_name="order_messages")
    op.drop_index("ix_order_messages_sender_user_id", table_name="order_messages")
    op.drop_index("ix_order_messages_order_id", table_name="order_messages")
    op.drop_table("order_messages")
