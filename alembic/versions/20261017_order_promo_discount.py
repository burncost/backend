"""orders.promo_discount

Additive migration only: adds `promo_discount` to `orders` so the flat Naira
promo amount applied at checkout is persisted alongside `promo_code`. Previously
the amount was only discoverable by re-deriving it from the totals, which made
the order summary show the platform fee net of the promo. Default 0 = no promo,
so existing orders keep their current behaviour. No column/table is dropped,
renamed or type-changed.

Revision ID: 20261017_order_promo_discount
Revises: 20261016_promo_max_uses_per_user
Create Date: 2026-10-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261017_order_promo_discount"
down_revision = "20261016_promo_max_uses_per_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("promo_discount", sa.Numeric(15, 2), nullable=False, server_default="0"),
    )

    # Backfill legacy orders whose promo amount was never persisted. The amount
    # applied at checkout is `min(promo.discount_amount, eligible_base)`, where
    # the eligible base depends on the code's scope. Reconstructing it here lets
    # the checkout summary show the buyer's real platform fee (not net of the
    # promo) for orders placed before this column existed. Only rows carrying a
    # promo_code and still sitting at the default 0 are touched.
    op.execute(sa.text(
        """
        UPDATE orders o
        SET promo_discount = LEAST(
            p.discount_amount,
            CASE p.scope_type
                WHEN 'product' THEN COALESCE((
                    SELECT SUM(oi.total_price) FROM order_items oi
                    WHERE oi.order_id = o.id AND oi.product_id = p.scope_id
                ), 0)
                WHEN 'vendor' THEN COALESCE((
                    SELECT SUM(oi.total_price) FROM order_items oi
                    WHERE oi.order_id = o.id AND oi.vendor_id = p.scope_id
                ), 0)
                WHEN 'category' THEN COALESCE((
                    SELECT SUM(oi.total_price) FROM order_items oi
                    JOIN products pr ON pr.id = oi.product_id
                    WHERE oi.order_id = o.id AND pr.category_id = p.scope_id
                ), 0)
                ELSE o.subtotal
            END
        )
        FROM promo_codes p
        WHERE o.promo_code = p.code
          AND COALESCE(o.promo_discount, 0) = 0
        """
    ))


def downgrade() -> None:
    op.drop_column("orders", "promo_discount")
