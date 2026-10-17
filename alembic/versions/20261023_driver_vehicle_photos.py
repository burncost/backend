"""add vehicle onboarding photos to driver_profiles

Additive migration only: five NULLABLE columns on the existing
``driver_profiles`` table backing the driver-onboarding requirement that a
driver documents the whole vehicle (front, back and both sides) before any
delivery job can be accepted.

Each photo column holds the stored URL of an already-normalised image
(<= 0.5 MB, longest edge 1920 px - see ``app/utils/image_processing.py``), so
no image bytes ever live in the database.

No columns/tables used by auth, onboarding, orders, payments or the delivery
lifecycle are dropped, renamed or type-changed. Existing driver rows simply
have NULL photos and are blocked by the accept gate until they upload them.

Revision ID: 20261023_driver_vehicle_photos
Revises: 20261022_delivery_job_events_proof_types
Create Date: 2026-10-17 00:00:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20261023_driver_vehicle_photos"
down_revision = "20261022_delivery_job_events_proof_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE driver_profiles ADD COLUMN IF NOT EXISTS vehicle_photo_front VARCHAR(1000)")
    op.execute("ALTER TABLE driver_profiles ADD COLUMN IF NOT EXISTS vehicle_photo_back VARCHAR(1000)")
    op.execute("ALTER TABLE driver_profiles ADD COLUMN IF NOT EXISTS vehicle_photo_left VARCHAR(1000)")
    op.execute("ALTER TABLE driver_profiles ADD COLUMN IF NOT EXISTS vehicle_photo_right VARCHAR(1000)")
    op.execute("ALTER TABLE driver_profiles ADD COLUMN IF NOT EXISTS vehicle_photos_updated_at TIMESTAMP")


def downgrade() -> None:
    for column in (
        "vehicle_photo_front",
        "vehicle_photo_back",
        "vehicle_photo_left",
        "vehicle_photo_right",
        "vehicle_photos_updated_at",
    ):
        op.execute(f"ALTER TABLE driver_profiles DROP COLUMN IF EXISTS {column}")
