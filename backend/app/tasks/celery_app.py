from celery import Celery
from celery.schedules import timedelta

from app.config import settings

celery_app = Celery(
    "energypulse",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # Fetch fresh EIA prices every 6 hours
        "eia-ingestion-every-6h": {
            "task": "app.tasks.ingestion_tasks.run_eia_ingestion",
            "schedule": timedelta(hours=6),
        },
        # Regenerate AI summaries every 6 hours (offset slightly so data is ready)
        "summary-refresh-every-6h": {
            "task": "app.tasks.summary_tasks.refresh_all_summaries",
            "schedule": timedelta(hours=6, minutes=15),
        },
    },
)

celery_app.autodiscover_tasks([
    "app.tasks.ingestion_tasks",
    "app.tasks.summary_tasks",
])
