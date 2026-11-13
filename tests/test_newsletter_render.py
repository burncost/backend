"""Newsletter merge-tag rendering.

Contract pinned here:
  * `{{tag}}` substitution happens in one pass, so a recipient value containing
    a tag cannot inject another merge field (or a link).
  * Merge values are HTML-escaped in the HTML body; the `{{price_table}}` block
    built by the renderer is trusted and stays markup.
  * Unknown tags vanish instead of reaching an inbox as `{{typo}}`.
  * The subject is single-line (header-injection guard) and never blank.
  * Every message carries the shell + one-click unsubscribe when a token exists.
"""
from app.services.newsletter_render import (
    MERGE_TAGS,
    build_price_table,
    html_to_text,
    recipient_context,
    render_email,
    render_string,
)

RECIPIENT = {
    "email": "Ada@Example.com",
    "name": "Ada Obi",
    "business_name": "Ada Building Supplies",
    "city": "Ikeja",
    "state": "Lagos",
    "tier": "verified_vendor",
    "unsubscribe_token": "tok-123",
}

ITEMS = [
    {"name": "Dangote Cement 50kg", "spec": "Grade 42.5", "unit": "bag", "low": 7000, "high": 8200},
    {"name": "Bua Cement", "spec": None, "unit": "bag", "low": 6900, "high": 6900},
]


def test_known_tags_use_recipient_values():
    context = recipient_context(RECIPIENT)
    assert render_string("Hi {{first_name}}", context) == "Hi Ada"
    assert render_string("{{business_name}}", context) == "Ada Building Supplies"
    assert render_string("{{city}}, {{state}}", context) == "Ikeja, Lagos"


def test_substitution_is_single_pass():
    """A name that looks like a tag must not be expanded again."""
    out = render_string("Hi {{first_name}}", {"first_name": "{{unsubscribe_url}}", "unsubscribe_url": "/x"})
    assert out == "Hi {{unsubscribe_url}}"


def test_unknown_tags_are_dropped():
    assert render_string("Hello {{nope}}!", RECIPIENT) == "Hello !"


def test_every_advertised_tag_is_documented():
    for tag in MERGE_TAGS:
        assert tag in recipient_context(RECIPIENT, price_items=ITEMS)


def test_recipient_context_falls_back_to_first_word():
    context = recipient_context({"email": "x@y.co", "name": "Chinedu Nwosu"})
    assert context["first_name"] == "Chinedu"
    assert context["last_name"] == "Nwosu"
    assert context["unsubscribe_url"] == ""  # no token -> no dead link


def test_price_table_renders_ranges_and_is_empty_without_items():
    table = build_price_table(ITEMS)
    assert "Dangote Cement 50kg" in table
    assert "Grade 42.5" in table
    assert "₦7,000 – ₦8,200" in table
    assert "₦6,900" in table  # low == high collapses to one figure
    assert build_price_table([]) == ""


def test_render_email_builds_subject_body_and_unsubscribe():
    campaign = {
        "subject": "  {{first_name}},\n this week at Burncost ",
        "body_html": "<p>Hello {{first_name}} ({{email}})</p>{{price_table}}",
        "body_text": None,
    }
    rendered = render_email(campaign, RECIPIENT, base_url="https://burncost.com", price_items=ITEMS)

    assert rendered.subject == "Ada, this week at Burncost"
    assert "\n" not in rendered.subject
    assert "Hello Ada (ada@example.com)" in rendered.html
    assert "Dangote Cement 50kg" in rendered.html
    assert "https://burncost.com/unsubscribe?token=tok-123" in rendered.html
    assert "Dangote Cement 50kg" in rendered.text


def test_render_email_escapes_merge_values_but_keeps_price_markup():
    rendered = render_email(
        {"subject": "Hi", "body_html": "<p>{{first_name}}</p>{{price_table}}", "body_text": None},
        {"email": "x@y.co", "first_name": "<script>alert(1)</script>", "unsubscribe_token": "t"},
        base_url="https://burncost.com",
        price_items=ITEMS,
    )
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "<table" in rendered.html


def test_blank_subject_and_body_fall_back_safely():
    rendered = render_email(
        {"subject": "   ", "body_html": "<p>{{first_name}}</p>", "body_text": ""},
        {"email": "x@y.co", "first_name": "Ada"},
    )
    assert rendered.subject == "Burncost update"
    assert "BURNCOST" in rendered.html


def test_body_text_template_is_rendered_and_used_for_preheader():
    rendered = render_email(
        {"subject": "Hi", "body_html": "<p>{{business_name}}</p>", "body_text": "Text for {{first_name}}"},
        RECIPIENT,
    )
    assert rendered.text == "Text for Ada"
    assert rendered.html.count("Text for Ada") == 1  # hidden preheader only


def test_html_to_text_strips_markup_and_blocks():
    text = html_to_text("<h2>Title</h2><p>One &amp; two</p><ul><li>Item</li></ul>")
    assert "Title" in text
    assert "One & two" in text
    assert "<" not in text
