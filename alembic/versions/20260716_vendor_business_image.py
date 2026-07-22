"""16th July - Add business_image column to vendors table

Revision ID: 20260716_vendor_business_image
Revises: 20260716_vendor_drafts
Create Date: 2026-07-16 05:41:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = '20260716_vendor_business_image'
down_revision = '20260716_vendor_drafts'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS business_image TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE vendors DROP COLUMN IF EXISTS business_image")