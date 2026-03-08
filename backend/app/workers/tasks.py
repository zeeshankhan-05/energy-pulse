"""Celery worker tasks for EnergyPulse background data collection and analysis."""

import logging

from app.workers.celery_app import celery_app
from app.database import SessionLocal
from app.services.normalization import normalize_pipeline

logger = logging.getLogger(__name__)


def _record_failed_job(task_name: str, exc: Exception) -> None:
    """Log a persistent record of the failed job for ops visibility."""
    logger.error(
        "FAILED JOB [%s]: %s — will retry if retries remain",
        task_name, exc,
    )


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
        _record_failed_job("scrape_puc_data_task", exc)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(name="tasks.detect_anomalies_task", bind=True, max_retries=3)
def detect_anomalies_task(self) -> dict:
    """Scan price_snapshots for statistical anomalies and persist Alert records.

    Calls run_anomaly_detection() with a managed DB session.

    Returns:
        Summary dict from run_anomaly_detection.
    """
    # Import here to keep the import tree clean in the module-level scope
    from app.services.anomaly_detection import run_anomaly_detection

    db = SessionLocal()
    try:
        logger.info("detect_anomalies_task: starting anomaly detection run")
        summary = run_anomaly_detection(db)
        logger.info(
            "detect_anomalies_task: configs=%d anomalies=%d alerts_created=%d skipped=%d",
            summary["configs_evaluated"],
            summary["anomalies_detected"],
            summary["alerts_created"],
            summary["alerts_skipped_dedup"],
        )
        return summary
    except Exception as exc:
        _record_failed_job("detect_anomalies_task", exc)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(name="tasks.deliver_alerts_task", bind=True, max_retries=5)
def deliver_alerts_task(self) -> dict:
    """Deliver all pending alert notifications via email and/or Slack.

    Calls deliver_pending_alerts() with a managed DB session.

    Returns:
        Summary dict from deliver_pending_alerts.
    """
    from app.services.alert_delivery import deliver_pending_alerts

    db = SessionLocal()
    try:
        logger.info("deliver_alerts_task: starting alert delivery run")
        summary = deliver_pending_alerts(db)
        logger.info(
            "deliver_alerts_task: processed=%d email=%d slack=%d failures=%d",
            summary["alerts_processed"],
            summary["email_sent"],
            summary["slack_sent"],
            summary["delivery_failures"],
        )
        return summary
    except Exception as exc:
        _record_failed_job("deliver_alerts_task", exc)
        raise self.retry(exc=exc, countdown=120)
    finally:
        db.close()
