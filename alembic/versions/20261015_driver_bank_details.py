"""add driver payout/bank details to driver_profiles

Additive migration only: adds four NULLABLE columns to the existing
`driver_profiles` table so an independent driver can supply the single bank
account their earnings are settled into. Nullable columns are safe on a live
table - existing rows keep working, and a driver can onboard and deliver before
adding payout details.

No new table is created: a driver is settled into exactly one account, so the
columns live on the profile row (1:1 with the driver's user) and reuse the same
column types as `vendor_bank_accounts` so the payout layer can treat both
identically. No column is dropped, renamed or type-changed.

Revision ID: 20261015_driver_bank_details
Revises: 20260908_driver_earnings
Create Date: 2026-10-15 00:00:00.000000

NOTE (manual application): apply by revision id after the driver-earnings
migration, e.g. `alembic upgrade 20261015_driver_bank_details`. See the Phase 1A
migration docstring about the multi-head history / `down_revision` repointing if
your database sits on the other lineage; plain `alembic upgrade head` will not
resolve while more than one head exists.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261015_driver_bank_details"
down_revision = "20260908_driver_earnings"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("bank_name", sa.String(length=100)),
    ("bank_account_number", sa.String(length=20)),
    ("bank_account_name", sa.String(length=255)),
    ("bank_code", sa.String(length=10)),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("driver_profiles", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("driver_profiles", name)
