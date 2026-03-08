"""Celery worker tasks for EnergyPulse background data collection."""

import logging

from celery import Celery

from app.config import settings
from app.database import SessionLocal
from app.services.normalization import normalize_pipeline

logger = logging.getLogger(__name__)

celery_app = Celery(
    "energypulse",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]


@celery_app.task(name="tasks.scrape_puc_data_task", bind=True, max_retries=3)
def scrape_puc_data_task(self, records: list[dict]) -> dict:
    """Normalize and persist a batch of PUC-scraped records.

    Args:
        records: List of raw price record dicts produced by a state scraper.

    Returns:
        normalize_pipeline summary dict.
    """
    db = SessionLocal()
    try:
        logger.info("scrape_puc_data_task: received %d records", len(records))
        summary = normalize_pipeline(records, db)
        logger.info(
            "scrape_puc_data_task: inserted=%d rejected=%d duplicates=%d",
            summary["inserted"],
            summary["rejected"],
            summary["duplicates"],
        )
        return summary
    except Exception as exc:
        logger.error("scrape_puc_data_task failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
