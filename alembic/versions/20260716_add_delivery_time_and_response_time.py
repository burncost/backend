"""add delivery_time and response_time columns to vendors

Revision ID: 20260716_add_delivery_time
Revises: e99d1b3752b7
Create Date: 2026-07-16 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260716_add_delivery_time"
down_revision: Union[str, None] = "516af8f95124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='vendors' AND column_name='response_time'
            ) THEN
                ALTER TABLE vendors ADD COLUMN response_time VARCHAR(100) DEFAULT '< 1 hour';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_column("vendors", "response_time")
