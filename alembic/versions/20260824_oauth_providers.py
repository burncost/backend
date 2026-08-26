"""add oauth provider fields to users.

Revision ID: 20260824_oauth_providers
Revises: 20260824_ai_procurement_data_provenance
Create Date: 2026-08-24 19:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260824_oauth_providers"
down_revision = "20260824_ai_procurement_data_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Allow OAuth-only accounts (no phone/password required).
    op.alter_column("users", "phone_number", existing_type=sa.String(length=20), nullable=True)
    op.alter_column("users", "password_hash", existing_type=sa.String(length=255), nullable=True)

    # New OAuth provider fields.
    op.add_column("users", sa.Column("auth_provider", sa.String(length=20), nullable=True, server_default="email"))
    op.add_column("users", sa.Column("oauth_id", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))
    op.create_index("ix_users_auth_provider", "users", ["auth_provider"])
    op.create_index("ix_users_oauth_id", "users", ["oauth_id"])


def downgrade() -> None:
    op.drop_index("ix_users_oauth_id", table_name="users")
    op.drop_index("ix_users_auth_provider", table_name="users")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "oauth_id")
    op.drop_column("users", "auth_provider")
    op.alter_column("users", "password_hash", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("users", "phone_number", existing_type=sa.String(length=20), nullable=False)