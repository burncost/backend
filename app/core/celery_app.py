from celery import Celery
from celery.schedules import crontab
from app.config import settings

celery_app = Celery(
    "building_materials_platform",
    broker=settings.CELERY_BROKER_URL or settings.REDIS_URL,
    backend=settings.CELERY_RESULT_BACKEND or settings.REDIS_URL,
    include=[
        "app.tasks.email_tasks",
        "app.tasks.document_processing_tasks",
        "app.tasks.boq_generation_tasks",
        "app.tasks.analytics_tasks",
        "app.tasks.newsletter_tasks",
    ]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
)

# Daily market price newsletter (Phase 3) — runs on the emails queue.
celery_app.conf.beat_schedule = {
    "send-price-of-day-daily": {
        "task": "send_price_of_day_newsletter",
        "schedule": crontab(hour=7, minute=0),
    },
}

# Task routes
celery_app.conf.task_routes = {
    "app.tasks.email_tasks.*": {"queue": "emails"},
    "app.tasks.document_processing_tasks.*": {"queue": "documents"},
    "app.tasks.boq_generation_tasks.*": {"queue": "boq"},
    "app.tasks.analytics_tasks.*": {"queue": "analytics"},
    "app.tasks.newsletter_tasks.*": {"queue": "emails"},
    "send_price_of_day_newsletter": {"queue": "emails"},
}
