"""Celery application instance and beat schedule.

Extracted from tasks.py to avoid circular imports when adding the beat
schedule.  The schedule uses **string task names** (e.g. "tasks.detect_…")
rather than importing task functions directly — this is the standard Celery
pattern to prevent circular import chains on worker startup.
"""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "energypulse",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]

# ---------------------------------------------------------------------------
# Beat schedule — uses string task names to avoid circular imports
# ---------------------------------------------------------------------------
celery_app.conf.beat_schedule = {
    "detect-anomalies": {
        "task": "tasks.detect_anomalies_task",
        "schedule": crontab(minute=0, hour="*/6"),  # every 6 hours
    },
    "deliver-alerts": {
        "task": "tasks.deliver_alerts_task",
        "schedule": crontab(minute="*/15"),  # every 15 minutes
    },
}
celery_app.conf.timezone = "UTC"
