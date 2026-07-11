"""Add token_usage and token_transactions tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-09 12:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid


revision: str = '0003'
down_revision: Union[str, None] = '83e7282dd87a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create token_usage table
    op.create_table(
        'token_usage',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('balance', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('lifetime_purchased', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('lifetime_consumed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('free_tier_used_this_month', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('free_tier_month', sa.String(7), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Create token_transactions table
    op.create_table(
        'token_transactions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('transaction_type', sa.Enum('purchase', 'consumption', 'refund', 'free_tier', 'expiry', name='transactiontype'), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('balance_after', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.String(50), nullable=True),
        sa.Column('boq_id', sa.String(50), nullable=True),
        sa.Column('reference', sa.String(100), nullable=True),
        sa.Column('description', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create indexes (idempotent — IF NOT EXISTS)
    op.execute('CREATE INDEX IF NOT EXISTS ix_token_usage_user_id ON token_usage (user_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_token_transactions_user_id ON token_transactions (user_id)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_token_transactions_created_at ON token_transactions (created_at)')


def downgrade() -> None:
    op.drop_table('token_transactions')
    op.drop_table('token_usage')
    op.execute('DROP TYPE IF EXISTS transactiontype')
