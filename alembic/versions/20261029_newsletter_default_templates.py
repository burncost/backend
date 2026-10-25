"""Seed the built-in newsletter templates

The admin console can create templates, but an empty list on a fresh database
means every first campaign starts from a blank body. These three are the copy
the team actually sends, keyed on ``slug`` so re-running (or a second
environment) never duplicates them.

Plain SQL literals (not bound parameters) so the revision also renders under
``alembic upgrade --sql`` for review.

Revision ID: 20261029_newsletter_default_templates
Revises: 20261028_newsletter_recipient_merge_fields
Create Date: 2026-10-29
"""
import uuid

from alembic import op

revision = "20261029_newsletter_default_templates"
down_revision = "20261028_newsletter_recipient_merge_fields"
branch_labels = None
depends_on = None


# slug -> (name, audience, subject, body_html)
TEMPLATES = (
    (
        "weekly-price-update",
        "Weekly price update",
        "vendors",
        "{{first_name}}, the material prices in {{state}} this week",
        "<p>Hi {{first_name}},</p>"
        "<p>Here are the price ranges buyers on Burncost are seeing this week:</p>"
        "{{price_table}}"
        "<p>Keep {{business_name}} in front of those buyers by refreshing your stock.</p>"
        "<p>&mdash; The Burncost team<br /><a href=\"{{site_url}}\">{{site_url}}</a></p>"
        "<p style=\"font-size:12px;color:#666\">You get this because you opted in to "
        "market updates. <a href=\"{{unsubscribe_url}}\">Unsubscribe</a>.</p>",
    ),
    (
        "new-materials",
        "New materials added",
        "vendors",
        "{{first_name}}, new materials buyers are searching for",
        "<p>Hi {{first_name}},</p>"
        "<p>We added new materials to the catalogue in {{state}}. Buyers are already "
        "searching for them &mdash; if you stock any, add them to {{business_name}} "
        "today.</p>"
        "<p><a href=\"{{site_url}}\">Update your catalogue</a></p>"
        "<p>&mdash; The Burncost team</p>"
        "<p style=\"font-size:12px;color:#666\">You get this because you opted in to "
        "market updates. <a href=\"{{unsubscribe_url}}\">Unsubscribe</a>.</p>",
    ),
    (
        "welcome-buyers",
        "Welcome (buyers)",
        "customers",
        "{{first_name}}, price a delivery before you order",
        "<p>Hi {{first_name}},</p>"
        "<p>Comparing quotes is easier when you know what delivery should cost. "
        "Build a free delivery map on Burncost before your next order.</p>"
        "<p><a href=\"{{site_url}}\">Start a delivery map</a></p>"
        "<p>&mdash; The Burncost team</p>"
        "<p style=\"font-size:12px;color:#666\">You get this because you opted in to "
        "market updates. <a href=\"{{unsubscribe_url}}\">Unsubscribe</a>.</p>",
    ),
)


def _lit(value: str) -> str:
    """SQL string literal (single quotes doubled, no parameters)."""
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    for slug, name, audience, subject, body_html in TEMPLATES:
        op.execute(
            "INSERT INTO newsletter_templates"
            " (id, name, slug, subject, body_html, body_text, audience,"
            "  is_active, created_at, updated_at)"
            " VALUES ("
            f"{_lit(str(uuid.uuid4()))}, {_lit(name)}, {_lit(slug)}, {_lit(subject)}, "
            f"{_lit(body_html)}, NULL, {_lit(audience)}, TRUE, now(), now())"
            " ON CONFLICT (slug) DO NOTHING"
        )


def downgrade() -> None:
    for slug, *_ in TEMPLATES:
        op.execute(f"DELETE FROM newsletter_templates WHERE slug = {_lit(slug)}")
