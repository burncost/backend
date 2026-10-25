"""Newsletter campaigns, templates and the per-campaign send log

Additive tables only - nothing existing is altered. Server defaults are set on
the status/counter columns so a raw INSERT (or an older ORM build) still lands a
valid row.

Revision ID: 20261027_newsletter_campaigns
Revises: 20261026_blog_posts
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261027_newsletter_campaigns"
down_revision = "20261026_blog_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "newsletter_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("audience", sa.String(length=20), nullable=False, server_default="vendors"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_newsletter_templates_slug", "newsletter_templates", ["slug"], unique=True)
    op.create_index("ix_newsletter_templates_audience", "newsletter_templates", ["audience"])

    op.create_table(
        "newsletter_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("newsletter_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("segment", sa.String(length=50), nullable=False, server_default="vendors_all"),
        sa.Column("segment_params", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("total_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "blog_post_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("blog_posts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_newsletter_campaigns_template_id", "newsletter_campaigns", ["template_id"])
    op.create_index("ix_newsletter_campaigns_segment", "newsletter_campaigns", ["segment"])
    op.create_index("ix_newsletter_campaigns_status", "newsletter_campaigns", ["status"])
    op.create_index("ix_newsletter_campaigns_scheduled_at", "newsletter_campaigns", ["scheduled_at"])
    op.create_index("ix_newsletter_campaigns_blog_post_id", "newsletter_campaigns", ["blog_post_id"])

    op.create_table(
        "newsletter_recipients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("newsletter_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("business_name", sa.String(length=255), nullable=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vendors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "campaign_id", "email", name="uq_newsletter_recipients_campaign_email"
        ),
    )
    op.create_index("ix_newsletter_recipients_campaign_id", "newsletter_recipients", ["campaign_id"])
    op.create_index("ix_newsletter_recipients_email", "newsletter_recipients", ["email"])
    op.create_index("ix_newsletter_recipients_status", "newsletter_recipients", ["status"])
    op.create_index(
        "ix_newsletter_recipients_campaign_status",
        "newsletter_recipients",
        ["campaign_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_newsletter_recipients_campaign_status", table_name="newsletter_recipients")
    op.drop_index("ix_newsletter_recipients_status", table_name="newsletter_recipients")
    op.drop_index("ix_newsletter_recipients_email", table_name="newsletter_recipients")
    op.drop_index("ix_newsletter_recipients_campaign_id", table_name="newsletter_recipients")
    op.drop_table("newsletter_recipients")

    op.drop_index("ix_newsletter_campaigns_blog_post_id", table_name="newsletter_campaigns")
    op.drop_index("ix_newsletter_campaigns_scheduled_at", table_name="newsletter_campaigns")
    op.drop_index("ix_newsletter_campaigns_status", table_name="newsletter_campaigns")
    op.drop_index("ix_newsletter_campaigns_segment", table_name="newsletter_campaigns")
    op.drop_index("ix_newsletter_campaigns_template_id", table_name="newsletter_campaigns")
    op.drop_table("newsletter_campaigns")

    op.drop_index("ix_newsletter_templates_audience", table_name="newsletter_templates")
    op.drop_index("ix_newsletter_templates_slug", table_name="newsletter_templates")
    op.drop_table("newsletter_templates")

