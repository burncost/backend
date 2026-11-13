"""Campaign service rules that need no database.

Pinned here: segment validation fails loudly and normalises input, hard
opt-outs are recognised, campaign bodies are sanitised on write, and the
serialisers expose the fields the admin console reads.
"""
from types import SimpleNamespace

import pytest

from app.services.newsletter_campaign_service import (
    SEGMENT_CUSTOM_EMAILS,
    SEGMENT_MARKETING_OPT_IN,
    SEGMENT_VENDORS_ALL,
    SEGMENT_VENDORS_BY_STATE,
    SEGMENT_VENDORS_BY_TIER,
    CampaignStateError,
    NewsletterError,
    _clean_body_html,
    _clean_subject,
    _ensure_editable,
    _suppressed,
    campaign_to_dict,
    normalize_emails,
    recipient_to_dict,
    validate_segment,
)


def test_normalize_emails_lowercases_dedupes_and_keeps_order():
    assert normalize_emails("B@x.com, a@x.com; b@x.com") == ["b@x.com", "a@x.com"]
    assert normalize_emails(["A@x.com", "a@x.com"]) == ["a@x.com"]


def test_normalize_emails_rejects_invalid_addresses():
    with pytest.raises(NewsletterError):
        normalize_emails("not-an-email")


def test_validate_segment_defaults_and_normalises_params():
    assert validate_segment(None, None) == (SEGMENT_VENDORS_ALL, {})
    assert validate_segment(SEGMENT_VENDORS_BY_STATE, {"state": " Lagos "}) == (
        SEGMENT_VENDORS_BY_STATE,
        {"state": "Lagos"},
    )
    assert validate_segment(SEGMENT_VENDORS_BY_TIER, {"tier": "VERIFIED_VENDOR"}) == (
        SEGMENT_VENDORS_BY_TIER,
        {"tier": "verified_vendor"},
    )
    assert validate_segment(SEGMENT_CUSTOM_EMAILS, {"emails": "a@x.com b@x.com"}) == (
        SEGMENT_CUSTOM_EMAILS,
        {"emails": ["a@x.com", "b@x.com"]},
    )


@pytest.mark.parametrize(
    "segment, params",
    [
        ("unknown_segment", None),
        (SEGMENT_VENDORS_BY_STATE, {}),
        (SEGMENT_VENDORS_BY_TIER, {"tier": "platinum"}),
        (SEGMENT_CUSTOM_EMAILS, {"emails": "   "}),
    ],
)
def test_validate_segment_rejects_bad_input(segment, params):
    with pytest.raises(NewsletterError):
        validate_segment(segment, params)


def test_suppressed_needs_a_real_opt_out():
    default = SimpleNamespace(opted_out_at=None, marketing_opt_in=False)
    opted_out = SimpleNamespace(opted_out_at=object(), marketing_opt_in=False)
    re_opted_in = SimpleNamespace(opted_out_at=object(), marketing_opt_in=True)
    assert _suppressed(None) is False
    assert _suppressed(default) is False
    assert _suppressed(opted_out) is True
    assert _suppressed(re_opted_in) is False


def test_marketing_opt_in_segment_needs_no_params():
    assert validate_segment(SEGMENT_MARKETING_OPT_IN, None) == (SEGMENT_MARKETING_OPT_IN, {})


def test_subject_is_single_line_and_required():
    assert _clean_subject("  Hello\n\n world  ") == "Hello world"
    with pytest.raises(NewsletterError):
        _clean_subject("   ")


def test_body_is_sanitised_and_required():
    body = _clean_body_html("<p>Hi</p><script>alert(1)</script>")
    assert "<script>" not in body
    assert "<p>Hi</p>" in body
    with pytest.raises(NewsletterError):
        _clean_body_html("<script>alert(1)</script>")


def test_only_editable_statuses_accept_changes():
    for status in ("draft", "scheduled", "failed", "cancelled"):
        _ensure_editable(SimpleNamespace(status=status))
    for status in ("queued", "sending", "sent"):
        with pytest.raises(CampaignStateError):
            _ensure_editable(SimpleNamespace(status=status))


def test_campaign_serialiser_exposes_admin_fields():
    campaign = SimpleNamespace(
        id="c1", name="Autumn deals", subject="Hi", segment=SEGMENT_VENDORS_ALL,
        segment_params={}, status="draft", dry_run=False, template_id=None,
        blog_post_id=None, scheduled_at=None, started_at=None, finished_at=None,
        total_recipients=3, sent_count=1, failed_count=1, skipped_count=1,
        last_error="boom", created_by=None, created_at=None, updated_at=None,
        body_html="<p>Hi</p>", body_text=None,
    )
    data = campaign_to_dict(campaign)
    assert data["id"] == "c1"
    assert data["status"] == "draft"
    assert data["body_text"] == ""
    assert "body_html" not in campaign_to_dict(campaign, include_body=False)


def test_recipient_serialiser_defaults_optional_fields():
    recipient = SimpleNamespace(
        id="r1", email="a@x.com", name=None, business_name=None, state=None,
        status="pending", attempts=0, error=None, sent_at=None,
    )
    assert recipient_to_dict(recipient)["name"] == ""
    assert recipient_to_dict(recipient)["sent_at"] is None
