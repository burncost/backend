"""add geotag/freshness verification columns to delivery_proofs.

Additive migration only: adds nullable verification columns to `delivery_proofs`
so a captured photo's EXIF-derived capture time + GPS, geofence distance and the
resulting verdict (verified | unverified | out_of_range) can be persisted. No
existing column/table is dropped, renamed or type-changed, and older rows simply
read back with NULLs.

Revision ID: 20260910_delivery_proof_exif
Revises: 20260909_merge_delivery_heads
Create Date: 2026-09-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260910_delivery_proof_exif"
down_revision = "20260909_merge_delivery_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("delivery_proofs", sa.Column("exif_taken_at", sa.DateTime(), nullable=True))
    op.add_column("delivery_proofs", sa.Column("exif_lat", sa.Numeric(11, 8), nullable=True))
    op.add_column("delivery_proofs", sa.Column("exif_lng", sa.Numeric(11, 8), nullable=True))
    op.add_column("delivery_proofs", sa.Column("gps_source", sa.String(length=20), nullable=True))
    op.add_column("delivery_proofs", sa.Column("geofence_distance_m", sa.Numeric(12, 2), nullable=True))
    op.add_column("delivery_proofs", sa.Column("geofence_radius_m", sa.Numeric(12, 2), nullable=True))
    op.add_column("delivery_proofs", sa.Column("within_geofence", sa.Boolean(), nullable=True))
    op.add_column("delivery_proofs", sa.Column("is_live", sa.Boolean(), server_default=sa.false(), nullable=True))
    op.add_column(
        "delivery_proofs",
        sa.Column("needs_review", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("delivery_proofs", sa.Column("verification_status", sa.String(length=20), nullable=True))
    op.add_column("delivery_proofs", sa.Column("verification_note", sa.Text(), nullable=True))
    op.add_column("delivery_proofs", sa.Column("raw_exif", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("delivery_proofs", "raw_exif")
    op.drop_column("delivery_proofs", "verification_note")
    op.drop_column("delivery_proofs", "verification_status")
    op.drop_column("delivery_proofs", "needs_review")
    op.drop_column("delivery_proofs", "is_live")
    op.drop_column("delivery_proofs", "within_geofence")
    op.drop_column("delivery_proofs", "geofence_radius_m")
    op.drop_column("delivery_proofs", "geofence_distance_m")
    op.drop_column("delivery_proofs", "gps_source")
    op.drop_column("delivery_proofs", "exif_lng")
    op.drop_column("delivery_proofs", "exif_lat")
    op.drop_column("delivery_proofs", "exif_taken_at")
