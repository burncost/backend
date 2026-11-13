"""Campaign sender orchestration (`app/tasks/newsletter_tasks.py`).

The service primitives (`claim_batch`, `record_sent`, …) are covered by the
Phase 2 tests, so they are scripted here; what is pinned is the wiring the
worker owns:

  * a batch is claimed, rendered per recipient with real merge values, sent, and
    only then acknowledged - and the loop drains until nothing is pending;
  * an address that unsubscribed after the audience was frozen is skipped, not
    mailed;
  * a dry run never touches the provider but still closes the campaign;
  * a failed send is retried while the attempt budget lasts, then parked as
    failed;
  * a campaign cancelled mid-send stops delivering;
  * the dispatcher freezes due campaigns and hands their ids to the sender, and
    parks a segment that resolved to nobody as failed instead of retrying.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import newsletter_campaign_service as campaigns
from app.tasks import newsletter_tasks as tasks


class _Result:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    """Only what the sender touches: get/commit/flush/refresh/execute."""

    def __init__(self, campaign, stale_ids=None):
        self.campaign = campaign
        self.stale_ids = list(stale_ids or [])
        self.commits = 0
        self.flushes = 0
        self.refreshes = 0

    async def get(self, model, pk):
        return self.campaign

    async def execute(self, stmt):
        return _Result(rows=self.stale_ids)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        self.flushes += 1

    async def refresh(self, obj):
        self.refreshes += 1


class _SessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc_info):
        return False


class _Notifier:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    async def send_email(self, to, subject, html_content, cc=None):
        self.sent.append({"to": to, "subject": subject, "html": html_content})
        return self.ok


def _campaign(**overrides):
    base = dict(
        id=uuid4(),
        subject="Hi {{first_name}}",
        body_html="<p>{{business_name}} in {{state}}</p>",
        body_text=None,
        status="queued",
        dry_run=False,
        started_at=None,
        finished_at=None,
        last_error=None,
        total_recipients=0,
        sent_count=0,
        failed_count=0,
        skipped_count=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _row(email="ada@example.com", name="Ada Okafor", business="Ada Building Supplies", attempts=1):
    return {
        "id": uuid4(),
        "email": email,
        "name": name,
        "business_name": business,
        "city": "Lagos",
        "state": "Lagos",
        "tier": "verified_vendor",
        "unsubscribe_token": f"tok-{email}",
        "attempts": attempts,
    }


def _wire(monkeypatch, campaign, *, batches, notifier=None, opted_out=None, stale_ids=None):
    """Script the service primitives and return (session, actions, notifier)."""
    session = _Session(campaign, stale_ids=stale_ids)
    notifier = notifier or _Notifier()
    actions = []
    pending = list(batches)

    async def fake_claim(db, campaign_id, limit=None):
        return pending.pop(0) if pending else []

    async def fake_sent(db, recipient_id):
        actions.append(("sent", recipient_id))

    async def fake_failure(db, recipient_id, error, *, attempts):
        status = "failed" if attempts >= 3 else "pending"
        actions.append((status, recipient_id))
        return status

    async def fake_skip(db, recipient_id, reason):
        actions.append(("skipped", recipient_id))

    async def fake_counts(db, campaign_row):
        # Recipient rows end in the status of their LAST write (a retried row is
        # pending, not pending+failed).
        latest = {}
        for status, recipient_id in actions:
            latest[recipient_id] = status
        counted = {"pending": 0, "sending": 0, "sent": 0, "failed": 0, "skipped": 0}
        for status in latest.values():
            counted[status] += 1
        counted["total"] = len(latest)
        return counted

    async def no_prices(db, limit=6):
        return []

    async def fake_opted_out(db, emails):
        return set(opted_out or ())

    monkeypatch.setattr(tasks, "AsyncSessionLocal", _SessionFactory(session))
    monkeypatch.setattr(tasks, "NotificationService", lambda: notifier)
    monkeypatch.setattr(tasks, "_opted_out_emails", fake_opted_out)
    monkeypatch.setattr(campaigns, "claim_batch", fake_claim)
    monkeypatch.setattr(campaigns, "record_sent", fake_sent)
    monkeypatch.setattr(campaigns, "record_failure", fake_failure)
    monkeypatch.setattr(campaigns, "mark_skipped", fake_skip)
    monkeypatch.setattr(campaigns, "refresh_counts", fake_counts)
    monkeypatch.setattr(campaigns, "price_of_day_items", no_prices)
    return session, actions, notifier


# ── sender ────────────────────────────────────────────────────────────────────


def test_sender_delivers_rendered_messages_and_closes_as_sent(monkeypatch):
    campaign = _campaign()
    ada, bola = _row(), _row(email="bola@example.com", name="Bola Ade", business="Bola Cement")
    session, actions, notifier = _wire(monkeypatch, campaign, batches=[[ada, bola]])

    result = asyncio.run(tasks._send_campaign(str(campaign.id)))

    assert [message["to"] for message in notifier.sent] == ["ada@example.com", "bola@example.com"]
    assert [message["subject"] for message in notifier.sent] == ["Hi Ada", "Hi Bola"]
    assert "Ada Building Supplies in Lagos" in notifier.sent[0]["html"]
    assert "unsubscribe?token=tok-ada@example.com" in notifier.sent[0]["html"]

    assert [status for status, _ in actions] == ["sent", "sent"]
    assert result["sent"] == 2 and result["failed"] == 0
    assert result["status"] == "sent"
    assert campaign.status == "sent" and campaign.finished_at is not None
    assert campaign.started_at is not None  # mark_sending ran
    assert session.commits >= 3  # mark_sending, each batch, finalize


def test_sender_skips_an_address_that_unsubscribed_after_queueing(monkeypatch):
    campaign = _campaign()
    ada, bola = _row(), _row(email="bola@example.com", name="Bola Ade")
    _, actions, notifier = _wire(
        monkeypatch, campaign, batches=[[ada, bola]], opted_out={"ada@example.com"}
    )

    result = asyncio.run(tasks._send_campaign(str(campaign.id)))

    assert [message["to"] for message in notifier.sent] == ["bola@example.com"]
    assert actions == [("skipped", ada["id"]), ("sent", bola["id"])]
    assert result["skipped"] == 1 and result["sent"] == 1


def test_dry_run_never_calls_the_provider_but_still_closes(monkeypatch):
    campaign = _campaign(dry_run=True)
    rows = [_row(), _row(email="bola@example.com")]
    session, actions, notifier = _wire(monkeypatch, campaign, batches=[rows])

    result = asyncio.run(tasks._send_campaign(str(campaign.id)))

    assert notifier.sent == []
    assert [status for status, _ in actions] == ["skipped", "skipped"]
    assert result["dry_run"] is True and result["skipped"] == 2
    assert campaign.status == "sent"  # a dry run is a completed rehearsal


def test_sender_retries_until_the_attempt_budget_is_spent(monkeypatch):
    campaign = _campaign()
    # One recipient, claimed three times: the id is what the retry loop keys on.
    base = _row()
    rows = [dict(base, attempts=n) for n in (1, 2, 3)]
    _, actions, _ = _wire(
        monkeypatch, campaign, batches=[[row] for row in rows], notifier=_Notifier(ok=False)
    )

    result = asyncio.run(tasks._send_campaign(str(campaign.id)))

    assert [status for status, _ in actions] == ["pending", "pending", "failed"]
    assert result["retried"] == 2 and result["failed"] == 1
    # Nothing delivered -> the campaign is parked as failed with a reason.
    assert campaign.status == "failed"
    assert campaign.last_error


def test_sender_parks_a_recipient_after_the_last_attempt(monkeypatch):
    campaign = _campaign()
    row = _row(attempts=3)
    _, actions, _ = _wire(
        monkeypatch, campaign, batches=[[row]], notifier=_Notifier(ok=False)
    )

    result = asyncio.run(tasks._send_campaign(str(campaign.id)))

    assert actions == [("failed", row["id"])]
    assert result["failed"] == 1 and result["retried"] == 0
    assert campaign.status == "failed"


def test_sender_stops_delivering_when_the_campaign_is_cancelled(monkeypatch):
    campaign = _campaign()
    session, _, notifier = _wire(monkeypatch, campaign, batches=[[_row()]])

    original_refresh = session.refresh

    async def refresh_then_cancel(obj):
        await original_refresh(obj)
        if obj.status == "sending":
            obj.status = "cancelled"

    session.refresh = refresh_then_cancel

    result = asyncio.run(tasks._send_campaign(str(campaign.id)))

    assert notifier.sent == []  # refresh before the first claim already saw it
    assert campaign.status == "cancelled"
    assert result["sent"] == 0


def test_sender_releases_a_batch_claimed_just_as_the_campaign_is_cancelled(monkeypatch):
    """A cancel that lands between claim and send releases the rows, no mail."""
    campaign = _campaign()
    row = _row()
    session, actions, notifier = _wire(monkeypatch, campaign, batches=[[row]])

    async def claim_then_cancel(db, campaign_id, limit=None):
        campaign.status = "cancelled"  # the admin's request committed first
        return [row]

    monkeypatch.setattr(campaigns, "claim_batch", claim_then_cancel)

    result = asyncio.run(tasks._send_campaign(str(campaign.id)))

    assert notifier.sent == []
    assert actions == [("skipped", row["id"])]
    assert result["skipped"] == 1 and result["sent"] == 0
    assert campaign.status == "cancelled"


def test_sender_ignores_a_campaign_that_is_not_queued(monkeypatch):
    campaign = _campaign(status="draft")
    session, actions, notifier = _wire(monkeypatch, campaign, batches=[[_row()]])

    result = asyncio.run(tasks._send_campaign(str(campaign.id)))

    assert result["skipped"] is True and "draft" in result["reason"]
    assert notifier.sent == [] and actions == [] and session.commits == 0



# ── dispatcher ────────────────────────────────────────────────────────────────


def _wire_dispatch(monkeypatch, campaign, *, due_ids, queue=None, stale_ids=None):
    session = _Session(campaign, stale_ids=stale_ids)
    handed = []

    async def fake_due(db, *, now=None, limit=20):
        return list(due_ids)

    async def fake_queue(db, row):
        row.status = "queued"
        return {"queued": 1, "total": 1}

    monkeypatch.setattr(tasks, "AsyncSessionLocal", _SessionFactory(session))
    monkeypatch.setattr(campaigns, "due_campaign_ids", fake_due)
    monkeypatch.setattr(campaigns, "queue_campaign", queue or fake_queue)
    monkeypatch.setattr(
        tasks, "send_newsletter_campaign_task", SimpleNamespace(delay=handed.append)
    )
    return session, handed


def test_dispatcher_queues_a_due_campaign_then_hands_it_to_the_sender(monkeypatch):
    campaign = _campaign(status="scheduled")
    session, handed = _wire_dispatch(monkeypatch, campaign, due_ids=[campaign.id])

    result = asyncio.run(tasks._dispatch_due())

    assert campaign.status == "queued"
    assert result["queued"] == [str(campaign.id)]
    assert handed == [str(campaign.id)]
    assert session.commits >= 1  # the frozen audience is persisted before .delay


def test_dispatcher_resumes_a_campaign_abandoned_by_a_dead_worker(monkeypatch):
    campaign = _campaign(status="sending")
    _, handed = _wire_dispatch(
        monkeypatch, campaign, due_ids=[], stale_ids=[campaign.id]
    )

    result = asyncio.run(tasks._dispatch_due())

    assert result["queued"] == []
    assert result["resumed"] == [str(campaign.id)]
    assert handed == [str(campaign.id)]


def test_dispatcher_parks_a_due_campaign_whose_segment_is_empty(monkeypatch):
    campaign = _campaign(status="scheduled")

    async def empty(db, row):
        raise campaigns.EmptyAudience("This segment resolves to no recipients")

    session, handed = _wire_dispatch(
        monkeypatch, campaign, due_ids=[campaign.id], queue=empty
    )

    result = asyncio.run(tasks._dispatch_due())

    assert result["failed"] == [str(campaign.id)]
    assert handed == []  # nothing to send
    assert campaign.status == "failed"
    assert "no recipients" in campaign.last_error
    assert session.commits >= 1


# ── opt-out wiring (email_prefs endpoint helper) ───────────────────────────────


class _CaptureSession:
    def __init__(self, rowcount=3):
        self.rowcount = rowcount
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return SimpleNamespace(rowcount=self.rowcount)


def test_unsubscribing_skips_the_pending_recipient_rows():
    from app.api.v1.endpoints import email_prefs

    session = _CaptureSession()
    changed = asyncio.run(email_prefs._stop_pending_sends(session, "  Ada@Example.com "))

    assert changed == 3
    sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "newsletter_recipients" in sql
    assert "ada@example.com" in sql  # normalised, so a casing mismatch cannot slip through
    assert "skipped" in sql


def test_unsubscribing_with_a_blank_address_touches_nothing():
    from app.api.v1.endpoints import email_prefs

    session = _CaptureSession()
    assert asyncio.run(email_prefs._stop_pending_sends(session, "   ")) == 0
    assert session.statements == []
