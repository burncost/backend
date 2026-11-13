"""Admin newsletter API (`/admin/newsletters`): the contracts the console needs.

Pinned here, without a database or a provider:

  * The composer contract: the segments/merge-tag catalogue is one call, and a
    bad segment or an empty-after-sanitising body is a 400 before anything is
    written.
  * Error mapping: `NewsletterError` -> 400, `CampaignStateError` -> 409, an
    unknown campaign -> 404, and no commit happens on any of them.
  * Send-now passes `when=None` down to the service (which freezes the audience
    in the same transaction) and commits exactly once.
  * Preview renders real merge values for a stand-in recipient, never leaks a
    `{{tag}}`, never ships an unsubscribe token and writes nothing.
  * A sent campaign cannot be deleted (its recipients are the send log); a draft
    can.
  * Templates are sanitised on write, like blog content.

Endpoints are called directly with a scripted session, matching
`test_promo_vendor_manage.py` / `test_admin_settings_effective.py`.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import admin_newsletter as api
from app.config import settings
from app.services import newsletter_campaign_service as campaigns

ADMIN = SimpleNamespace(id=uuid4(), role="admin")


# ── scripted session ──────────────────────────────────────────────────────────


class _Result:
    """Enough of `Result` for count/scalars/group-by reads."""

    def __init__(self, *, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _FakeSession:
    def __init__(self, *, get_result=None, results=None):
        self._get = get_result
        self._results = list(results or [])
        self.added = []
        self.deleted = []
        self.commits = 0
        self.flushes = 0
        self.refreshed = []

    async def get(self, model, pk):
        return self._get

    async def execute(self, stmt):
        if not self._results:
            raise AssertionError("unexpected extra db.execute() call")
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def commit(self):
        self.commits += 1

    async def delete(self, obj):
        self.deleted.append(obj)


def _campaign(**overrides):
    base = dict(
        id=uuid4(),
        name="October update",
        subject="Hi {{first_name}}",
        body_html="<p>Hello {{business_name}} in {{state}}</p>",
        body_text=None,
        status="draft",
        dry_run=False,
        segment=campaigns.SEGMENT_VENDORS_ALL,
        segment_params={},
        template_id=None,
        blog_post_id=None,
        scheduled_at=None,
        started_at=None,
        finished_at=None,
        total_recipients=0,
        sent_count=0,
        failed_count=0,
        skipped_count=0,
        last_error=None,
        created_by=None,
        created_at=None,
        updated_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── catalogue ─────────────────────────────────────────────────────────────────


def test_segments_catalogue_is_one_call_for_the_composer():
    result = asyncio.run(api.list_segments(current_user=ADMIN))

    ids = {segment["id"] for segment in result["segments"]}
    assert campaigns.SEGMENT_VENDORS_BY_STATE in ids
    assert campaigns.SEGMENT_CUSTOM_EMAILS in ids
    assert "price_table" in result["merge_tags"]
    assert result["limits"]["max_recipients"] == int(settings.NEWSLETTER_MAX_RECIPIENTS)


# ── create / patch / delete ───────────────────────────────────────────────────


def test_create_campaign_rejects_an_unknown_segment_as_400():
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.create_campaign(
            payload=api.CampaignCreate(
                subject="Hello", body_html="<p>hi</p>", segment="everybody"
            ),
            current_user=ADMIN,
            db=db,
        ))

    assert exc.value.status_code == 400
    assert "Unknown segment" in exc.value.detail
    assert db.added == [] and db.commits == 0


def test_create_campaign_rejects_a_body_that_sanitises_to_nothing():
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.create_campaign(
            payload=api.CampaignCreate(subject="Hello", body_html="<script>x()</script>"),
            current_user=ADMIN,
            db=db,
        ))

    assert exc.value.status_code == 400
    assert "body" in str(exc.value.detail).lower()
    assert db.commits == 0


def test_create_campaign_stores_an_aware_schedule_as_naive_utc():
    """`scheduled_at` columns are naive; the beat compares them against utcnow()."""
    when = datetime(2026, 11, 1, 11, 0, tzinfo=timezone(timedelta(hours=2)))
    db = _FakeSession()

    result = asyncio.run(api.create_campaign(
        payload=api.CampaignCreate(
            subject="  Hello   vendor ",
            body_html="<p>hi</p>",
            scheduled_at=when,
        ),
        current_user=ADMIN,
        db=db,
    ))

    assert result["scheduled_at"] == "2026-11-01T09:00:00"
    assert result["status"] == "scheduled"
    assert result["subject"] == "Hello vendor"
    assert db.commits == 1 and len(db.added) == 1


def test_patch_a_queued_campaign_is_409_and_writes_nothing():
    campaign = _campaign(status="queued", total_recipients=12)
    db = _FakeSession(get_result=campaign)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.update_campaign(
            campaign.id,
            api.CampaignUpdate(subject="Changed after queueing"),
            current_user=ADMIN,
            db=db,
        ))

    assert exc.value.status_code == 409
    assert campaign.subject == "Hi {{first_name}}"
    assert db.commits == 0


def test_patch_with_a_state_segment_and_no_state_is_400():
    campaign = _campaign()
    db = _FakeSession(get_result=campaign)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.update_campaign(
            campaign.id,
            api.CampaignUpdate(segment=campaigns.SEGMENT_VENDORS_BY_STATE),
            current_user=ADMIN,
            db=db,
        ))

    assert exc.value.status_code == 400
    assert "state" in str(exc.value.detail)
    assert campaign.segment == campaigns.SEGMENT_VENDORS_ALL
    assert db.commits == 0


def test_unknown_campaign_is_404():
    db = _FakeSession(get_result=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.get_campaign(uuid4(), current_user=ADMIN, db=db))

    assert exc.value.status_code == 404


def test_delete_a_sent_campaign_is_409_because_it_is_the_send_log():
    campaign = _campaign(status="sent", sent_count=40)
    db = _FakeSession(get_result=campaign)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.delete_campaign(campaign.id, current_user=ADMIN, db=db))

    assert exc.value.status_code == 409
    assert db.deleted == [] and db.commits == 0


def test_delete_a_draft_campaign_commits_once():
    campaign = _campaign()
    db = _FakeSession(get_result=campaign)

    assert asyncio.run(api.delete_campaign(campaign.id, current_user=ADMIN, db=db)) is None
    assert db.deleted == [campaign]
    assert db.commits == 1



# ── send control ──────────────────────────────────────────────────────────────


def test_send_now_passes_no_time_and_commits_once(monkeypatch):
    """Send-now must reach `queue_campaign` (audience frozen), not just set a date."""
    campaign = _campaign()
    seen = {}

    async def fake_schedule(db, camp, when):
        seen["when"] = when
        camp.status = "queued"
        camp.total_recipients = 7
        return camp

    monkeypatch.setattr(api.campaigns, "schedule_campaign", fake_schedule)
    db = _FakeSession(get_result=campaign)

    result = asyncio.run(api.schedule_campaign(
        campaign.id, payload=None, current_user=ADMIN, db=db
    ))

    assert seen["when"] is None
    assert result["status"] == "queued"
    assert result["total_recipients"] == 7
    assert db.commits == 1


def test_schedule_maps_an_empty_audience_to_400(monkeypatch):
    async def empty(db, camp, when):
        raise campaigns.EmptyAudience("This segment resolves to nobody")

    monkeypatch.setattr(api.campaigns, "schedule_campaign", empty)
    db = _FakeSession(get_result=_campaign())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.schedule_campaign(uuid4(), payload=None, current_user=ADMIN, db=db))

    assert exc.value.status_code == 400
    assert db.commits == 0


def test_schedule_maps_a_state_error_to_409(monkeypatch):
    async def already_queued(db, camp, when):
        raise campaigns.CampaignStateError("This campaign is already queued")

    monkeypatch.setattr(api.campaigns, "schedule_campaign", already_queued)
    db = _FakeSession(get_result=_campaign(status="sending"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.schedule_campaign(uuid4(), payload=None, current_user=ADMIN, db=db))

    assert exc.value.status_code == 409
    assert db.commits == 0


def test_audience_preview_maps_an_oversized_segment_to_400(monkeypatch):
    async def too_big(db, segment, params=None, *, sample=5):
        raise campaigns.AudienceTooLarge("This segment resolves to 9000 recipients")

    monkeypatch.setattr(api.campaigns, "preview_audience", too_big)
    db = _FakeSession(get_result=_campaign())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.preview_campaign_audience(
            uuid4(), sample=5, current_user=ADMIN, db=db
        ))

    assert exc.value.status_code == 400
    assert "9000" in str(exc.value.detail)


# ── preview ───────────────────────────────────────────────────────────────────


def test_preview_renders_merge_values_and_writes_nothing(monkeypatch):
    campaign = _campaign()

    async def fake_audience(db, segment, params=None, *, sample=5):
        return {
            "segment": segment,
            "segment_params": {},
            "count": 3,
            "max_recipients": 5000,
            "sample": [],
        }

    async def no_prices(db, limit=6):
        return []

    monkeypatch.setattr(api.campaigns, "preview_audience", fake_audience)
    monkeypatch.setattr(api.campaigns, "price_of_day_items", no_prices)
    db = _FakeSession(get_result=campaign)

    result = asyncio.run(api.preview_campaign(
        campaign.id, payload=None, current_user=ADMIN, db=db
    ))

    assert result["subject"] == "Hi Ada"
    assert "Ada Building Supplies" in result["html"]
    assert "{{" not in result["subject"] and "{{" not in result["html"]
    # No token for a stand-in recipient: no dead unsubscribe link, no writes.
    assert "unsubscribe?token=" not in result["html"]
    assert result["audience"]["count"] == 3
    assert db.commits == 0 and db.added == [] and db.flushes == 0


# ── frozen send log ───────────────────────────────────────────────────────────


def test_recipients_returns_rows_and_status_counts():
    campaign = _campaign()
    row = SimpleNamespace(
        id=uuid4(),
        email="ada@example.com",
        name="Ada Okafor",
        business_name="Ada Building Supplies",
        state="Lagos",
        status="sent",
        attempts=1,
        error=None,
        sent_at=None,
    )
    db = _FakeSession(
        get_result=campaign,
        results=[_Result(scalar=1), _Result(rows=[row]), _Result(rows=[("sent", 1)])],
    )

    result = asyncio.run(api.list_campaign_recipients(
        campaign.id,
        page=1,
        page_size=50,
        status_filter=None,
        current_user=ADMIN,
        db=db,
    ))

    assert result["counts"] == {"sent": 1}
    assert result["total"] == 1
    assert result["recipients"][0]["email"] == "ada@example.com"
    assert result["recipients"][0]["status"] == "sent"


# ── templates ─────────────────────────────────────────────────────────────────


def test_template_create_rejects_a_body_that_sanitises_to_nothing():
    db = _FakeSession()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.create_template(
            api.TemplateCreate(name="Price of day", body_html="<script>x()</script>"),
            current_user=ADMIN,
            db=db,
        ))

    assert exc.value.status_code == 400
    assert db.added == [] and db.commits == 0


def test_template_create_sanitises_and_normalises_the_audience():
    db = _FakeSession()

    result = asyncio.run(api.create_template(
        api.TemplateCreate(
            name="  Price   of day ",
            subject=" Today's   prices ",
            body_html='<p onclick="steal()">hi</p><script>bad()</script>',
            audience="Vendors",
        ),
        current_user=ADMIN,
        db=db,
    ))

    assert result["name"] == "Price of day"
    assert result["subject"] == "Today's prices"
    assert result["audience"] == "vendors"
    assert result["body_html"].strip() == "<p>hi</p>"
    assert db.commits == 1 and len(db.added) == 1


def test_template_create_rejects_an_unknown_audience():
    db = _FakeSession()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.create_template(
            api.TemplateCreate(name="Something", audience="everybody"),
            current_user=ADMIN,
            db=db,
        ))

    assert exc.value.status_code == 400
    assert "audience" in str(exc.value.detail).lower()
    assert db.commits == 0


def test_unknown_template_is_404():
    db = _FakeSession(get_result=None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.delete_template(uuid4(), current_user=ADMIN, db=db))

    assert exc.value.status_code == 404
    assert db.deleted == []


def test_naive_utc_leaves_naive_values_untouched():
    naive = datetime(2026, 11, 1, 9, 0)

    assert api._naive_utc(None) is None
    assert api._naive_utc(naive) == naive
    assert api._naive_utc(
        datetime(2026, 11, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3)))
    ) == datetime(2026, 11, 1, 12, 0)




# ── cancel ────────────────────────────────────────────────────────────────────


def test_cancel_a_sent_campaign_is_409_and_sends_nothing():
    campaign = _campaign(status="sent", sent_count=40)
    db = _FakeSession(get_result=campaign)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.cancel_campaign(campaign.id, current_user=ADMIN, db=db))

    assert exc.value.status_code == 409
    assert campaign.status == "sent"
    assert db.commits == 0


def test_cancel_is_idempotent_for_an_already_cancelled_campaign():
    campaign = _campaign(status="cancelled")
    db = _FakeSession(get_result=campaign)

    result = asyncio.run(api.cancel_campaign(campaign.id, current_user=ADMIN, db=db))

    assert result["status"] == "cancelled"
    assert db.commits == 1  # the endpoint's own commit; no extra recipient writes
