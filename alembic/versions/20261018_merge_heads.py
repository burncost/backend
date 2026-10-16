"""merge delivery + promo heads

The repository carried two divergent heads after the driver-bank-details and
order-promo-discount migrations were added on separate lineages:

  - 20261015_driver_bank_details
  - 20261017_order_promo_discount

Multiple heads make a plain ``alembic upgrade head`` abort with
"Multiple head revisions are present", forcing callers to name a revision
explicitly. This merge revision joins the two lineages into a single head so the
ordinary command works again. It performs no schema change - it only records the
join in ``alembic_version`` - so it is safe to apply on a database that already
sits on one or both of the joined revisions.

Revision ID: 20261018_merge_heads
Revises: 20261015_driver_bank_details, 20261017_order_promo_discount
Create Date: 2026-10-18 00:00:00.000000
"""

# revision identifiers, used by Alembic.
revision = "20261018_merge_heads"
down_revision = ("20261015_driver_bank_details", "20261017_order_promo_discount")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
