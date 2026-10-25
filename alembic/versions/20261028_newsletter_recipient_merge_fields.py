"""Freeze merge values and the unsubscribe token on newsletter recipients

The audience is resolved once, at queue time. Sending then never re-reads vendor
rows or preferences, so a later tier/state change (or opt-out) cannot alter what
an already-queued campaign says or where its unsubscribe link points. All four
columns are nullable - old rows simply render blank merge tags.

Revision ID: 20261028_newsletter_recipient_merge_fields
Revises: 20261027_newsletter_campaigns
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "20261028_newsletter_recipient_merge_fields"
down_revision = "20261027_newsletter_campaigns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("newsletter_recipients", sa.Column("city", sa.String(length=255), nullable=True))
    op.add_column("newsletter_recipients", sa.Column("state", sa.String(length=255), nullable=True))
    op.add_column("newsletter_recipients", sa.Column("tier", sa.String(length=20), nullable=True))
    op.add_column(
        "newsletter_recipients", sa.Column("unsubscribe_token", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("newsletter_recipients", "unsubscribe_token")
    op.drop_column("newsletter_recipients", "tier")
    op.drop_column("newsletter_recipients", "state")
    op.drop_column("newsletter_recipients", "city")
