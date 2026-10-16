"""add human review columns to delivery_proofs.

Additive migration only: adds nullable review columns to `delivery_proofs` so a
flagged proof (needs_review = true) can record a reviewer's outcome
(pending | cleared | rejected) without changing `needs_review` semantics. No
existing column/table is dropped, renamed or type-changed, and older rows simply
read back with NULLs.

Revision ID: 20260911_delivery_proof_review
Revises: 20260910_delivery_proof_exif
Create Date: 2026-09-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260911_delivery_proof_review"
down_revision = "20260910_delivery_proof_exif"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("delivery_proofs", sa.Column("review_status", sa.String(length=20), nullable=True))
    op.add_column("delivery_proofs", sa.Column("reviewed_by", sa.String(length=64), nullable=True))
    op.add_column("delivery_proofs", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.add_column("delivery_proofs", sa.Column("review_note", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("delivery_proofs", "review_note")
    op.drop_column("delivery_proofs", "reviewed_at")
    op.drop_column("delivery_proofs", "reviewed_by")
    op.drop_column("delivery_proofs", "review_status")
