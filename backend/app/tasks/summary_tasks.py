import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.price_snapshot import PriceSnapshot
from app.services.summary_service import generate_summary, should_regenerate

logger = logging.getLogger(__name__)


async def _async_refresh_all():
    """Inner async execution loop to check and refresh summaries for all regions."""
    try:
        async with AsyncSessionLocal() as db:
            # Get all distinct regions that have any data
            region_query = select(PriceSnapshot.region).distinct()
            result = await db.execute(region_query)
            regions = [r for (r,) in result.all()]
            
            refreshed_count = 0
            for region in regions:
                try:
                    needs_refresh = await should_regenerate(region, db)
                    if needs_refresh:
                        logger.info(f"Regenerating summary for {region}...")
                        await generate_summary(region, db, bypass_cache=True)
                        refreshed_count += 1
                    else:
                        logger.debug(f"Skipping summary generation for {region} (no significant changes).")
                except Exception as e:
                    logger.error(f"Failed to check/refresh summary for {region}: {e}")
            
            logger.info(f"Summary refresh complete. Regenerated {refreshed_count}/{len(regions)} regions.")
            
    except Exception as e:
        logger.error(f"Failed during _async_refresh_all: {e}")


def refresh_all_summaries():
    """
    Celery task entrypoint.
    Safely spins up an asyncio event loop since Celery operates synchronously
    and might already have an underlying loop depending on the execution pool.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_refresh_all())
    finally:
        loop.close()
