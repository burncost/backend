"""rename vendor TIN column to NIN

Vendor verification now keys on the National Identification Number (NIN) rather
than the Tax Identification Number (TIN). The existing `tax_identification_number`
column on `vendors` is renamed to `nin`.

Revision ID: 20260908_vendor_nin_rename
Revises: 20260907_driver_logistics
Create Date: 2026-09-08 12:00:00.000000
"""
from alembic import op

revision = "20260908_vendor_nin_rename"
down_revision = "20260907_driver_logistics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE vendors RENAME COLUMN tax_identification_number TO nin")


def downgrade() -> None:
    op.execute("ALTER TABLE vendors RENAME COLUMN nin TO tax_identification_number")