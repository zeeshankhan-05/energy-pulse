"""Celery tasks for EIA data ingestion."""

import asyncio
import logging

from app.tasks.celery_app import celery_app
from app.database import SessionLocal, AsyncSessionLocal
from app.services.data_ingestion import ingest_eia_data
from app.services.eia_client import DEFAULT_STATES

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.ingestion_tasks.run_eia_ingestion",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def run_eia_ingestion(self, states: list[str] | None = None) -> dict:
    """Fetch EIA prices for all default states and persist to the database.

    Retries up to 3 times with a 60-second delay on transient failures.
    Returns a summary dict with the count of inserted records.
    """
    states = states or DEFAULT_STATES
    logger.info("run_eia_ingestion starting for states: %s", states)

    db = SessionLocal()
    try:
        inserted = ingest_eia_data(db=db, states=states)
        db.commit()
        logger.info("run_eia_ingestion complete: %d records inserted", inserted)
        return {"inserted": inserted, "states": states}
    except Exception as exc:
        db.rollback()
        logger.error("run_eia_ingestion failed: %s", exc)
        raise self.retry(exc=exc)
    finally:
        db.close()


async def _ingest_async(states: list[str], months: int) -> int:
    """Run the sync ingestion pipeline inside an async session.

    AsyncSession.run_sync() establishes the greenlet context that asyncpg
    requires, then hands a regular sync Session to the callable. This lets
    ingest_eia_data / normalize_pipeline use db.query() / db.commit() etc.
    without any changes to those services.
    """
    async with AsyncSessionLocal() as session:
        inserted: int = await session.run_sync(
            lambda db: ingest_eia_data(db=db, states=states, months=months)
        )
    return inserted


def seed_initial_data(states: list[str] | None = None, months: int = 2) -> None:
    """Eagerly ingest EIA data without Celery — useful for seeding a fresh database.

    *months* controls how many months of history to fetch from the EIA API
    (passed as the ``length`` query param). Defaults to 2 for a fast seed run.

    Usage:
        python -c "from app.tasks.ingestion_tasks import seed_initial_data; seed_initial_data()"

    With options:
        python -c "
        from app.tasks.ingestion_tasks import seed_initial_data
        seed_initial_data(states=['IL', 'TX'], months=6)
        "
    """
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    states = states or DEFAULT_STATES
    logger.info("seed_initial_data: starting eager ingest for %s, months=%d", states, months)

    try:
        inserted = asyncio.run(_ingest_async(states, months=months))
        logger.info("seed_initial_data: done — %d records inserted", inserted)
    except Exception as exc:
        logger.error("seed_initial_data: failed — %s", exc)
        raise
