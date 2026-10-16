"""notifications.link

Additive migration only: adds a nullable `link` on `notifications` so a
notification can carry an in-app deep-link target (e.g. the admin review page
for a new supplier application). No existing column/table is dropped, renamed
or type-changed, and older rows simply read back NULL.

Revision ID: 20261007_notifications_link
Revises: 20261006_orders_promo_code
Create Date: 2026-10-07 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261007_notifications_link"
down_revision = "20261006_orders_promo_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("link", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "link")
