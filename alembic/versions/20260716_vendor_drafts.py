"""16th July - Vendor drafts table for cross-device registration resume

Revision ID: 20260716_vendor_drafts
Revises: a9c9cd13f49b
Create Date: 2026-07-16 04:26:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = '20260716_vendor_drafts'
down_revision = 'a9c9cd13f49b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS vendor_drafts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            current_step VARCHAR(50) DEFAULT 'business-info',
            business_info JSONB DEFAULT '{}'::jsonb,
            banking_info JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_vendor_drafts_user_id ON vendor_drafts (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vendor_drafts")