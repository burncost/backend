"""Merge-tag rendering and the branded email shell for newsletter campaigns.

Pure functions only - no DB session, no transport - so the same code renders an
admin preview and the real send, and it is cheap to unit test.

Design notes:
  * `{{tag}}` values are substituted in ONE regex pass. Substituted values are
    never re-scanned, so a recipient name containing `{{unsubscribe_url}}` can
    never inject a link.
  * Merge values are HTML-escaped for the HTML body (`price_table` is trusted:
    it is markup built by this module). Unknown tags are dropped so a recipient
    never sees a raw `{{typo}}` in a sent email.
  * The body itself is admin-authored and already sanitised on write (see
    `blog_service.sanitize_html`); only the wrapper is added here.
"""
from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, Mapping, Optional

# Tag -> human description. Exposed to the admin console as a hint list.
MERGE_TAGS: Dict[str, str] = {
    "first_name": "Recipient first name (first word of the name when unknown)",
    "last_name": "Recipient last name",
    "full_name": "Recipient full name",
    "business_name": "Recipient vendor/business name",
    "email": "Recipient email address",
    "city": "Vendor city",
    "state": "Vendor state",
    "tier": "Vendor verification tier (starter, verified_vendor, enterprise)",
    "site_url": "Marketing site root, e.g. https://burncost.com",
    "unsubscribe_url": "One-click unsubscribe link (blank for pasted addresses)",
    "price_table": "Today's material price table (HTML)",
    "year": "Current year",
}

# `price_table` is HTML produced here, not user input - keep it unescaped.
_TRUSTED_HTML_TAGS = frozenset({"price_table"})

_MERGE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr",
}
_MONEY = "₦"


@dataclass(frozen=True)
class RenderedEmail:
    """One recipient's final message, ready for NotificationService.send_email."""

    subject: str
    html: str
    text: str


def render_string(template: Optional[str], context: Mapping[str, Any]) -> str:
    """Substitute `{{tag}}` occurrences from `context` in a single pass.

    Missing/None values become an empty string. Keys are matched case-insensitively.
    """
    if not template:
        return ""
    lowered = {str(k).lower(): v for k, v in context.items()}

    def _sub(match: re.Match) -> str:
        value = lowered.get(match.group(1).lower())
        return "" if value is None else str(value)

    return _MERGE_RE.sub(_sub, template)


def _field(source: Any, key: str, default: str = "") -> str:
    """Read `key` from a dict-like or ORM object (campaign/template rows)."""
    if isinstance(source, Mapping):
        value = source.get(key)
    else:
        value = getattr(source, key, None)
    return default if value is None else str(value)


def _squash(text: str) -> str:
    return " ".join((text or "").split())


def _money(value: Any) -> str:
    try:
        return f"{_MONEY}{float(value):,.0f}"
    except (TypeError, ValueError):
        return "-"


def _price_range(item: Mapping[str, Any]) -> str:
    low, high = item.get("low"), item.get("high")
    if low is None and high is None:
        return "-"
    if low is None or high is None or abs(float(high) - float(low)) < 0.005:
        return _money(low if low is not None else high)
    return f"{_money(low)} – {_money(high)}"


def build_price_table(items: Optional[Iterable[Mapping[str, Any]]]) -> str:
    """Render `material_rates` range rows as the `{{price_table}}` block.

    Accepts the exact shape returned by `NewsletterService.build_price_of_day`
    (name/spec/unit/low/high) so both sends share one price source.
    """
    rows = []
    for item in items or []:
        name = html_lib.escape(str(item.get("name") or "Material"))
        extra = " · ".join(
            part for part in (str(item.get("spec") or "").strip(), str(item.get("unit") or "").strip()) if part
        )
        extra_html = (
            f'<br/><span style="color:#6b7280;font-size:12px">{html_lib.escape(extra)}</span>' if extra else ""
        )
        rows.append(
            "<tr>"
            f'<td style="padding:10px 12px;border-bottom:1px solid #ececec">{name}{extra_html}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #ececec;text-align:right;'
            f'white-space:nowrap;color:#059669;font-weight:600">{_price_range(item)}</td>'
            "</tr>"
        )
    if not rows:
        return ""
    return (
        '<table role="presentation" style="width:100%;border-collapse:collapse;font-size:14px">'
        "<thead><tr>"
        '<th style="text-align:left;padding:8px 12px;border-bottom:2px solid #ececec">Material</th>'
        '<th style="text-align:right;padding:8px 12px;border-bottom:2px solid #ececec">Range today</th>'
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )



class _TextExtractor(HTMLParser):
    """Minimal HTML -> plain text used when a campaign has no `body_text`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._drop_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._drop_depth = max(0, self._drop_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._drop_depth and data:
            self.parts.append(data)


def html_to_text(markup: Optional[str]) -> str:
    """Best-effort plain-text alternative (also used for the preheader snippet)."""
    if not markup:
        return ""
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def recipient_context(
    recipient: Mapping[str, Any],
    *,
    base_url: str = "",
    price_items: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Dict[str, str]:
    """Merge values for ONE recipient, as plain (unescaped) strings.

    `recipient` is an audience row from `newsletter_campaign_service` (email,
    name, first_name, last_name, business_name, city, state, tier,
    unsubscribe_token). Missing pieces degrade to blanks, never to `None`.
    """
    email = str(recipient.get("email") or "").strip().lower()
    full_name = str(recipient.get("name") or "").strip()
    first_name = str(recipient.get("first_name") or "").strip()
    last_name = str(recipient.get("last_name") or "").strip()
    if not first_name and full_name:
        first_name = full_name.split()[0]
    if not last_name and full_name and len(full_name.split()) > 1:
        last_name = full_name.split()[-1]

    site = (base_url or "").rstrip("/")
    token = str(recipient.get("unsubscribe_token") or "").strip()
    return {
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name or " ".join(p for p in (first_name, last_name) if p),
        "business_name": str(recipient.get("business_name") or "").strip(),
        "city": str(recipient.get("city") or "").strip(),
        "state": str(recipient.get("state") or "").strip(),
        "tier": str(recipient.get("tier") or "").strip(),
        "site_url": site,
        "unsubscribe_url": f"{site}/unsubscribe?token={token}" if token else "",
        "price_table": build_price_table(price_items),
        "year": str(datetime.utcnow().year),
    }


def _html_context(context: Mapping[str, str]) -> Dict[str, str]:
    return {
        key: (value if key in _TRUSTED_HTML_TAGS else html_lib.escape(str(value), quote=True))
        for key, value in context.items()
    }


def wrap_html(
    body_html: str,
    *,
    base_url: str = "",
    unsubscribe_url: str = "",
    preview_text: str = "",
) -> str:
    """Put campaign content inside the Burncost shell (header, footer, opt-out).

    Uses table-free inline-styled markup, which renders consistently in Gmail /
    Outlook and needs no external stylesheet (many clients strip them).
    """
    site = (base_url or "").rstrip("/")
    footer_link = (
        f'<a href="{html_lib.escape(unsubscribe_url, quote=True)}" '
        'style="color:#9ca3af;text-decoration:underline">Unsubscribe</a>'
        if unsubscribe_url else ""
    )
    preheader = (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">'
        f"{html_lib.escape(preview_text)}</div>"
        if preview_text else ""
    )
    home = html_lib.escape(f"{site}/" if site else "https://burncost.com", quote=True)
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f6f6f6">
{preheader}
<div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #ececec;border-radius:8px;padding:24px;font-family:Arial,Helvetica,sans-serif;color:#1e1e1e">
  <p style="margin:0 0 16px"><a href="{home}" style="color:#FF6B00;font-weight:bold;text-decoration:none;letter-spacing:1px">BURNCOST</a></p>
  <div style="font-size:15px;line-height:1.65">{body_html}</div>
  <p style="color:#9ca3af;font-size:11px;margin:24px 0 0">
    You are receiving this as a Burncost supplier update.
    {footer_link}
  </p>
</div>
</body></html>"""


def render_email(
    campaign: Any,
    recipient: Mapping[str, Any],
    *,
    base_url: str = "",
    price_items: Optional[Iterable[Mapping[str, Any]]] = None,
) -> RenderedEmail:
    """Render one campaign row for one recipient.

    `campaign` may be a `NewsletterCampaign` row or a dict with `subject`,
    `body_html`, `body_text`. The subject is whitespace-squashed (a newline in a
    subject is a header-injection risk) and never blank.
    """
    context = recipient_context(recipient, base_url=base_url, price_items=price_items)
    subject = _squash(render_string(_field(campaign, "subject"), context))[:255]
    body_html = render_string(_field(campaign, "body_html"), _html_context(context))
    body_text_raw = _field(campaign, "body_text")
    text = render_string(body_text_raw, context).strip() if body_text_raw.strip() else html_to_text(body_html)
    return RenderedEmail(
        subject=subject or "Burncost update",
        html=wrap_html(
            body_html,
            base_url=base_url,
            unsubscribe_url=context["unsubscribe_url"],
            preview_text=_squash(text)[:140],
        ),
        text=text,
    )
