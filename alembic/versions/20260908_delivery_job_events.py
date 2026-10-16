"""add delivery_job_events table (manual driver/vendor update feed).

Additive migration only:
- creates the new `delivery_job_events` table, its indexes and the two enum
  CHECK constraints (`actor_type`, `event_type`).

A `DeliveryJobEvent` is a human-authored update on a dispatch (driver/vendor
advancing a status, dropping a note or pinning a manual location) that is shown
to the buyer alongside the live map. It is deliberately separate from
`driver_location_updates` (the high-frequency GPS breadcrumb feed).

No columns/tables used by auth, onboarding, orders, payments or the delivery
lifecycle are dropped, renamed or type-changed.

Revision ID: 20260908_delivery_job_events
Revises: 20260908_vendor_tin_nin_split
Create Date: 2026-09-08 00:00:00.000000

NOTE (manual application): this revision attaches to the delivery-feature
lineage head `20260908_vendor_tin_nin_split` (the chain that includes
`20260907_driver_logistics` -> ... -> `20260908_payment_captures`). If your
database's `alembic_version` row is on the other lineage (`0fb735a539b3`)
instead, upgrade to `20260909_merge_delivery_heads` (the merge revision that
collapses both heads) or repoint `down_revision` before applying. Apply this by
its specific revision id, e.g. `alembic upgrade 20260908_delivery_job_events`.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260908_delivery_job_events"
down_revision = "20260908_vendor_tin_nin_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("delivery_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(length=20), server_default="driver", nullable=False),
        sa.Column("actor_name", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=20), server_default="status", nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("lat", sa.Numeric(11, 8), nullable=True),
        sa.Column("lng", sa.Numeric(11, 8), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["delivery_job_id"], ["delivery_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_delivery_job_events_delivery_job_id", "delivery_job_events", ["delivery_job_id"], unique=False
    )
    op.create_index("ix_delivery_job_events_order_id", "delivery_job_events", ["order_id"], unique=False)
    op.create_index("ix_delivery_job_events_created_at", "delivery_job_events", ["created_at"], unique=False)

    # Enum guards (mirror sql/create_all_tables.sql §49.8).
    op.create_check_constraint(
        "chk_delivery_job_events_actor_type",
        "delivery_job_events",
        "actor_type IS NULL OR actor_type IN ('driver','vendor','admin')",
    )
    op.create_check_constraint(
        "chk_delivery_job_events_event_type",
        "delivery_job_events",
        "event_type IS NULL OR event_type IN ('location','status','note')",
    )


def downgrade() -> None:
    op.drop_constraint("chk_delivery_job_events_event_type", "delivery_job_events", type_="check")
    op.drop_constraint("chk_delivery_job_events_actor_type", "delivery_job_events", type_="check")
    op.drop_index("ix_delivery_job_events_created_at", table_name="delivery_job_events")
    op.drop_index("ix_delivery_job_events_order_id", table_name="delivery_job_events")
    op.drop_index("ix_delivery_job_events_delivery_job_id", table_name="delivery_job_events")
    op.drop_table("delivery_job_events")
