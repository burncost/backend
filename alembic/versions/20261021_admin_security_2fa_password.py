"""admin security: password age + two-factor authentication columns.

Additive migration only — nothing is dropped, renamed or retyped, and every new
column is nullable so existing rows read back unchanged.

Backs the Admin → Settings → Security switches:

  ``password_changed_at``    set on every password write; drives the
                             "Password Expiration" toggle (NULL means "unknown",
                             so the code falls back to ``created_at``).
  ``two_factor_method``      'email' | 'totp'; NULL == not enrolled. This single
                             column is also the enrolment flag.
  ``two_factor_enabled_at``  when enrolment completed.
  ``totp_secret``            Fernet-encrypted base32 seed (key from SECRET_KEY).
  ``totp_recovery_codes``    JSON list of single-use recovery-code hashes.

Revision ID: 20261021_admin_security_2fa_password
Revises: 20261020_delivery_proof_buyer_confirmation
Create Date: 2026-10-21 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20261021_admin_security_2fa_password"
down_revision = "20261020_delivery_proof_buyer_confirmation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("two_factor_method", sa.String(length=10), nullable=True))
    op.add_column("users", sa.Column("two_factor_enabled_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("totp_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_recovery_codes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "totp_recovery_codes")
    op.drop_column("users", "totp_secret")
    op.drop_column("users", "two_factor_enabled_at")
    op.drop_column("users", "two_factor_method")
    op.drop_column("users", "password_changed_at")
