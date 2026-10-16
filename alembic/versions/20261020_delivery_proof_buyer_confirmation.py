"""add buyer receipt-confirmation columns to delivery_proofs.

Additive migration only: a POD photo that carries a capture time but no GPS
cannot be geofence-verified, so the buyer is asked to confirm receipt instead.
These columns persist that requirement and its outcome:

  - ``buyer_confirmation_required``  set by the POD scorer for a no-GPS photo
  - ``buyer_confirmed_at``           when the buyer confirmed receipt
  - ``buyer_confirmed_by``           the confirming buyer's user id

No existing column/table is dropped, renamed or type-changed; older rows simply
read back with the default (``FALSE`` / NULL).

Revision ID: 20261020_delivery_proof_buyer_confirmation
Revises: 20261019_order_picked_up_status
Create Date: 2026-10-20 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261020_delivery_proof_buyer_confirmation"
down_revision = "20261019_order_picked_up_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "delivery_proofs",
        sa.Column(
            "buyer_confirmation_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column("delivery_proofs", sa.Column("buyer_confirmed_at", sa.DateTime(), nullable=True))
    op.add_column("delivery_proofs", sa.Column("buyer_confirmed_by", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("delivery_proofs", "buyer_confirmed_by")
    op.drop_column("delivery_proofs", "buyer_confirmed_at")
    op.drop_column("delivery_proofs", "buyer_confirmation_required")
