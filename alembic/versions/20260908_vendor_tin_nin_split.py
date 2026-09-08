"""restore tax_identification_number and add a separate nin column

Corrects 20260908_vendor_nin_rename, which mislabeled the vendor TIN column as NIN
(`ALTER TABLE vendors RENAME COLUMN tax_identification_number TO nin`).

Vendor verification actually needs BOTH a Tax Identification Number and a National
Identification Number as independent fields. The data currently sitting in
`vendors.nin` is the old TIN data, so:
- rename `nin` back to `tax_identification_number` (preserves the existing values),
- add a fresh, empty nullable `nin` column to hold the real NIN going forward.

Revision ID: 20260908_vendor_tin_nin_split
Revises: 20260908_payment_captures
Create Date: 2026-09-08 00:00:00.000000

NOTE (manual application): the migration tree currently has two live heads. This
revision attaches to the head `20260908_payment_captures` (the lineage that includes
`20260908_vendor_nin_rename`). If your database's `alembic_version` row is on the
other lineage instead, repoint `down_revision` before applying. Apply it by its
specific revision id, e.g. `alembic upgrade 20260908_vendor_tin_nin_split`; plain
`alembic upgrade head` will not work while two heads exist.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260908_vendor_tin_nin_split"
down_revision = "20260908_payment_captures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Put the existing values (TIN data) back under their original column name...
    op.execute("ALTER TABLE vendors RENAME COLUMN nin TO tax_identification_number")
    # ...then add a new, separate NIN column (nullable, matches prior column type).
    op.execute("ALTER TABLE vendors ADD COLUMN nin VARCHAR(50)")


def downgrade() -> None:
    op.execute("ALTER TABLE vendors DROP COLUMN nin")
    op.execute("ALTER TABLE vendors RENAME COLUMN tax_identification_number TO nin")
