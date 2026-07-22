"""merge_delivery_time_head

Revision ID: d73761d596a2
Revises: 20260716_add_delivery_time, 516af8f95124
Create Date: 2026-07-16 10:33:01.533166

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers.
revision = 'd73761d596a2'
down_revision = ('20260716_add_delivery_time', '516af8f95124')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass