"""add audit_logs table and extend user_role enum

Revision ID: 20260809_audit_roles
Revises: 13b65519c4ad
Create Date: 2026-08-09 22:35:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers.
revision = '20260809_audit_roles'
down_revision = '13b65519c4ad'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Extend the native user_role enum with the new admin-tier roles.
    #    Postgres requires ALTER TYPE ... ADD VALUE; running each inside its
    #    own transaction to avoid the "unsafe use of new value" error.
    conn = op.get_bind()
    for role in ("manager", "support", "marketing"):
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = 'user_role' AND e.enumlabel = :label"),
            {"label": role},
        ).scalar()
        if not exists:
            op.execute(
                sa.text(f"ALTER TYPE user_role ADD VALUE '{role}'")
            )

    # 2) Add the persistent audit-log table
    op.create_table(
        'audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('resource_type', sa.String(length=100), nullable=True),
        sa.Column('resource_id', sa.String(length=255), nullable=True),
        sa.Column('method', sa.String(length=10), nullable=True),
        sa.Column('path', sa.String(length=500), nullable=True),
        sa.Column('status_code', sa.String(length=10), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'])
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'])


def downgrade() -> None:
    # Drop the audit-log table first
    op.drop_index('ix_audit_logs_created_at', table_name='audit_logs')
    op.drop_index('ix_audit_logs_action', table_name='audit_logs')
    op.drop_index('ix_audit_logs_user_id', table_name='audit_logs')
    op.drop_table('audit_logs')

    # Note: Postgres cannot remove an enum value without recreating the type.
    # Recreating the type would require re-routing the `users.role` column (and
    # any dependent casts/constraints), which is risky. The added enum values
    # are purely additive and backward-compatible, so downgrade leaves the
    # extended enum in place intentionally.