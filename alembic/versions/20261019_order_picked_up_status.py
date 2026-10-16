"""allow ``picked_up`` in the order status CHECK constraints

Adds ``picked_up`` to the two VARCHAR-backed order-status enum CHECK constraints
so the model value (``OrderStatus.PICKED_UP``) is accepted by the database:

  - ``orders.status``               (chk_orders_status)
  - ``order_items.vendor_status``   (chk_order_items_vendor_status)

``picked_up`` is the canonical order status once a driver collects the parcel;
``shipped`` is retained as a legacy value because historical rows still carry it.
Both are kept valid, so existing data is untouched.

The constraints only exist on databases created from ``sql/create_all_tables.sql``
(plain Alembic-built databases never created them), so the migration drops them
with ``IF EXISTS`` and re-adds them — a no-op on the latter. Legacy uppercase
values are normalized first, mirroring ``sql/fix_legacy_enum_values.sql``, so a
dirty row cannot make the ADD CONSTRAINT fail.

Revision ID: 20261019_order_picked_up_status
Revises: 20261018_merge_heads
Create Date: 2026-10-19 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261019_order_picked_up_status"
down_revision = "20261018_merge_heads"
branch_labels = None
depends_on = None


_ORDER_STATUSES = (
    "'pending_payment','payment_failed','confirmed','processing',"
    "'ready_for_pickup','picked_up','shipped','in_transit','delivered',"
    "'cancelled','refunded'"
)
# Identical value set: order_items.vendor_status shares the OrderStatus enum.
_ORDER_STATUSES_LEGACY = (
    "'pending_payment','payment_failed','confirmed','processing',"
    "'ready_for_pickup','shipped','in_transit','delivered',"
    "'cancelled','refunded'"
)


def _replace_constraint(table: str, constraint: str, column: str, values: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"))
    op.execute(sa.text(
        f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK ("
        f"{column} IS NULL OR ({column} IN ({values}) AND {column} = LOWER({column})))"
    ))


def upgrade() -> None:
    # Normalize any legacy uppercase values before enforcing the value set.
    op.execute(sa.text(
        "UPDATE orders SET status = LOWER(status) "
        "WHERE status IS NOT NULL AND status <> LOWER(status)"
    ))
    op.execute(sa.text(
        "UPDATE order_items SET vendor_status = LOWER(vendor_status) "
        "WHERE vendor_status IS NOT NULL AND vendor_status <> LOWER(vendor_status)"
    ))

    _replace_constraint("orders", "chk_orders_status", "status", _ORDER_STATUSES)
    _replace_constraint(
        "order_items", "chk_order_items_vendor_status", "vendor_status", _ORDER_STATUSES
    )


def downgrade() -> None:
    # Fold rows back onto the legacy value so the narrower CHECK still validates.
    op.execute(sa.text("UPDATE orders SET status = 'shipped' WHERE status = 'picked_up'"))
    op.execute(sa.text(
        "UPDATE order_items SET vendor_status = 'shipped' WHERE vendor_status = 'picked_up'"
    ))

    _replace_constraint("orders", "chk_orders_status", "status", _ORDER_STATUSES_LEGACY)
    _replace_constraint(
        "order_items", "chk_order_items_vendor_status", "vendor_status", _ORDER_STATUSES_LEGACY
    )
