"""promo_codes.scope

Additive migration only: adds `scope_type` and `scope_id` to `promo_codes` so a
promo can be narrowed to a whole platform, an owner's catalogue, a single
product or a category. Existing rows are back-filled: codes that already belong
to a vendor keep their current meaning (`vendor`), while vendor-less codes are
platform-wide. No column/table is dropped, renamed or type-changed.

Revision ID: 20261013_promo_scope
Revises: 20261012_negotiation_requested_price
Create Date: 2026-10-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "20261013_promo_scope"
down_revision = "20261012_negotiation_requested_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "promo_codes",
        sa.Column("scope_type", sa.String(length=20), nullable=False, server_default="platform"),
    )
    op.add_column(
        "promo_codes",
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
    )
    # Preserve the historical meaning: vendor-owned codes cover the vendor's
    # catalogue (scope "vendor"); the remaining platform codes stay "platform".
    op.execute("UPDATE promo_codes SET scope_type = 'vendor' WHERE vendor_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("promo_codes", "scope_id")
    op.drop_column("promo_codes", "scope_type")
