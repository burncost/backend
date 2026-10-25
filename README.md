# Burncost backend

FastAPI + SQLAlchemy (async) + Celery, Postgres (Aiven) and Redis.

## Processes

The API alone is not enough: anything scheduled or queued needs a Celery worker,
and recurring jobs need a beat process as well. Run all three (locally with
`.env` loaded, or `docker compose up` for the containerised versions):

```bash
# API
uvicorn app.main:app --reload --port 8080

# Worker - every queue (emails carries the newsletter campaign sender and the
# daily price-of-day mail; `celery` is the default queue for unrouted tasks)
celery -A app.core.celery_app worker -Q emails,documents,boq,analytics,celery -l info

# Beat - the clock for the beat_schedule entries (tick for due newsletter
# campaigns, daily price mail, expired-escrow release)
celery -A app.core.celery_app beat -l info
```

`docker compose up --build` starts `api`, `worker` and `beat` with the same
commands (see `docker-compose.yml`).

### Newsletter campaigns

- `send_due_newsletter_campaigns` runs every `NEWSLETTER_SCHEDULE_POLL_SECONDS`
  and freezes the audience of each due campaign; `send_newsletter_campaign` does
  the sending on the `emails` queue. Both are routed explicitly in
  `app/core/celery_app.py` (routes match task *names*).
- A campaign stuck in `queued`/`sending` with no worker is re-driven by the next
  dispatcher tick once it is older than `NEWSLETTER_STALE_MINUTES`.
- Verify the wiring without mailing anyone:

```bash
python -c "from app.core.celery_app import celery_app as c; print(list(c.conf.beat_schedule))"
python -c "from app.core.celery_app import celery_app as c; print(c.amqp.router.route({}, 'send_newsletter_campaign'))"
```

## Migrations

```bash
alembic upgrade head         # apply
alembic downgrade -1         # roll back the last revision
alembic upgrade head --sql   # render the pending SQL without touching the DB
```

Each environment (local, staging, production) needs `alembic upgrade head`
after a deploy; the app does not create tables at startup.

## Tests

```bash
python -m pytest tests -q
```

