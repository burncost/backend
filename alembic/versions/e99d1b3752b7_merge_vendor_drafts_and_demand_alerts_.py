"""merge_vendor_drafts_and_demand_alerts_heads

Revision ID: e99d1b3752b7
Revises: 20260716_vendor_business_image, 43a5d3c33488
Create Date: 2026-07-16 10:11:54.048401

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers.
revision = 'e99d1b3752b7'
down_revision = ('20260716_vendor_business_image', '43a5d3c33488')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass