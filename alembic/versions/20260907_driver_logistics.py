"""driver & delivery logistics: driver role + driver/delivery tables.

Phase 1 of the driver & delivery (Uber-for-construction-goods) feature.

Revision ID: 20260907_driver_logistics
Revises: 20260824_oauth_providers
Create Date: 2026-09-07 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260907_driver_logistics"
down_revision = "20260824_oauth_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Extend the native user_role enum with the driver role (additive).
    conn = op.get_bind()
    exists = conn.execute(
        sa.text("SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = 'user_role' AND e.enumlabel = :label"),
        {"label": "driver"},
    ).scalar()
    if not exists:
        op.execute(sa.text("ALTER TYPE user_role ADD VALUE 'driver'"))

    # 2) driver_profiles — driver identity, availability, default/live location.
    op.create_table(
        "driver_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("availability", sa.Boolean(), nullable=False),
        sa.Column("vehicle_type", sa.String(length=100), nullable=True),
        sa.Column("vehicle_plate", sa.String(length=50), nullable=True),
        sa.Column("vehicle_capacity", sa.String(length=100), nullable=True),
        sa.Column("license_no", sa.String(length=100), nullable=True),
        sa.Column("default_address", sa.String(length=500), nullable=True),
        sa.Column("default_city", sa.String(length=100), nullable=True),
        sa.Column("default_state", sa.String(length=100), nullable=True),
        sa.Column("default_lat", sa.Numeric(11, 8), nullable=True),
        sa.Column("default_lng", sa.Numeric(11, 8), nullable=True),
        sa.Column("current_lat", sa.Numeric(11, 8), nullable=True),
        sa.Column("current_lng", sa.Numeric(11, 8), nullable=True),
        sa.Column("current_location_updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_driver_profiles_user_id", "driver_profiles", ["user_id"], unique=True)
    op.create_index("ix_driver_profiles_vendor_id", "driver_profiles", ["vendor_id"])

    # 3) delivery_jobs — dispatch records for orders.
    op.create_table(
        "delivery_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_number", sa.String(length=50), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("driver_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignment_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("pickup_address", sa.String(length=500), nullable=True),
        sa.Column("pickup_city", sa.String(length=100), nullable=True),
        sa.Column("pickup_state", sa.String(length=100), nullable=True),
        sa.Column("pickup_lat", sa.Numeric(11, 8), nullable=True),
        sa.Column("pickup_lng", sa.Numeric(11, 8), nullable=True),
        sa.Column("dropoff_address", sa.String(length=500), nullable=True),
        sa.Column("dropoff_city", sa.String(length=100), nullable=True),
        sa.Column("dropoff_state", sa.String(length=100), nullable=True),
        sa.Column("dropoff_lat", sa.Numeric(11, 8), nullable=True),
        sa.Column("dropoff_lng", sa.Numeric(11, 8), nullable=True),
        sa.Column("driver_name", sa.String(length=255), nullable=True),
        sa.Column("driver_phone", sa.String(length=20), nullable=True),
        sa.Column("delivery_fee", sa.Numeric(15, 2), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(), nullable=True),
        sa.Column("in_transit_at", sa.DateTime(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["driver_profile_id"], ["driver_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delivery_jobs_job_number", "delivery_jobs", ["job_number"], unique=True)
    op.create_index("ix_delivery_jobs_order_id", "delivery_jobs", ["order_id"])
    op.create_index("ix_delivery_jobs_vendor_id", "delivery_jobs", ["vendor_id"])
    op.create_index("ix_delivery_jobs_driver_profile_id", "delivery_jobs", ["driver_profile_id"])
    op.create_index("ix_delivery_jobs_status", "delivery_jobs", ["status"])
    op.create_index("ix_delivery_jobs_created_at", "delivery_jobs", ["created_at"])

    # 4) driver_location_updates — time-series live-location feed.
    op.create_table(
        "driver_location_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("driver_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lat", sa.Numeric(11, 8), nullable=False),
        sa.Column("lng", sa.Numeric(11, 8), nullable=False),
        sa.Column("accuracy", sa.Numeric(11, 2), nullable=True),
        sa.Column("speed", sa.Numeric(11, 2), nullable=True),
        sa.Column("heading", sa.Numeric(11, 2), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["delivery_job_id"], ["delivery_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["driver_profile_id"], ["driver_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_driver_location_updates_driver_profile_id", "driver_location_updates", ["driver_profile_id"])
    op.create_index("ix_driver_location_updates_delivery_job_id", "driver_location_updates", ["delivery_job_id"])
    op.create_index("ix_driver_location_updates_recorded_at", "driver_location_updates", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_driver_location_updates_recorded_at", table_name="driver_location_updates")
    op.drop_index("ix_driver_location_updates_delivery_job_id", table_name="driver_location_updates")
    op.drop_index("ix_driver_location_updates_driver_profile_id", table_name="driver_location_updates")
    op.drop_table("driver_location_updates")

    op.drop_index("ix_delivery_jobs_created_at", table_name="delivery_jobs")
    op.drop_index("ix_delivery_jobs_status", table_name="delivery_jobs")
    op.drop_index("ix_delivery_jobs_driver_profile_id", table_name="delivery_jobs")
    op.drop_index("ix_delivery_jobs_vendor_id", table_name="delivery_jobs")
    op.drop_index("ix_delivery_jobs_order_id", table_name="delivery_jobs")
    op.drop_index("ix_delivery_jobs_job_number", table_name="delivery_jobs")
    op.drop_table("delivery_jobs")

    op.drop_index("ix_driver_profiles_vendor_id", table_name="driver_profiles")
    op.drop_index("ix_driver_profiles_user_id", table_name="driver_profiles")
    op.drop_table("driver_profiles")

    # Note: Postgres cannot remove an enum value without recreating the type,
    # so the added 'driver' role value is left in place intentionally (additive).