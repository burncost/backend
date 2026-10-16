"""allow ``proof`` / ``proof_review`` in the delivery_job_events event_type CHECK

Also allows ``buyer`` in the ``actor_type`` CHECK.

Two delivery-feed inserts were rejected by the CHECK constraints created with the
table by ``20260908_delivery_job_events`` (mirrored in
``sql/create_all_tables.sql`` §49.8):

  * ``event_type`` only allowed ``location`` / ``status`` / ``note``, while the
    POD submit path records ``proof`` and the proof-review path records
    ``proof_review``. The INSERT therefore failed with an IntegrityError on the
    *second* commit of ``POST /delivery/jobs/{id}/pod`` - after the photo row was
    already saved - so the driver saw a 500 and the buyer feed stayed empty.
  * ``actor_type`` only allowed ``driver`` / ``vendor`` / ``admin``, while the
    buyer's receipt confirmation records ``actor_type='buyer'`` (see
    ``_finalize_delivery_on_receipt``), which failed the same way on
    ``POST /orders/{id}/confirm-delivery``.

Both value sets are widened here to what the code actually writes. Purely
additive and data-safe: no row can carry a value outside the old sets, so there
is nothing to normalize. ``DROP CONSTRAINT IF EXISTS`` keeps the migration a
no-op on databases that never created the constraints from the DDL file.

Revision ID: 20261022_delivery_job_events_proof_types
Revises: 20261021_admin_security_2fa_password
Create Date: 2026-10-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261022_delivery_job_events_proof_types"
down_revision = "20261021_admin_security_2fa_password"
branch_labels = None
depends_on = None


_EVENT_TYPES = "'location','status','note','proof','proof_review'"
_EVENT_TYPES_LEGACY = "'location','status','note'"
_ACTOR_TYPES = "'driver','vendor','admin','buyer'"
_ACTOR_TYPES_LEGACY = "'driver','vendor','admin'"


def _replace_check(constraint: str, column: str, values: str) -> None:
    op.execute(sa.text(
        f"ALTER TABLE delivery_job_events DROP CONSTRAINT IF EXISTS {constraint}"
    ))
    op.execute(sa.text(
        f"ALTER TABLE delivery_job_events ADD CONSTRAINT {constraint} "
        f"CHECK ({column} IS NULL OR {column} IN ({values}))"
    ))


def upgrade() -> None:
    _replace_check("chk_delivery_job_events_event_type", "event_type", _EVENT_TYPES)
    _replace_check("chk_delivery_job_events_actor_type", "actor_type", _ACTOR_TYPES)


def downgrade() -> None:
    # Fold rows back onto a still-valid value so the narrower CHECKs can be added.
    # Unknown types are dropped; buyer-authored events are re-attributed rather
    # than deleted so the buyer's delivery feed is not silently emptied.
    op.execute(sa.text(
        "DELETE FROM delivery_job_events WHERE event_type IN ('proof','proof_review')"
    ))
    op.execute(sa.text(
        "UPDATE delivery_job_events SET actor_type = 'admin' WHERE actor_type = 'buyer'"
    ))
    _replace_check("chk_delivery_job_events_event_type", "event_type", _EVENT_TYPES_LEGACY)
    _replace_check("chk_delivery_job_events_actor_type", "actor_type", _ACTOR_TYPES_LEGACY)
