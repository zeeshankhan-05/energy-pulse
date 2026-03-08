"""Scraper registry and orchestration.

Usage:
    import asyncio
    from app.services.scrapers.scraper_registry import run_all_scrapers
    records = asyncio.run(run_all_scrapers())

Each scraper is run concurrently via asyncio.gather(); a failure in one state
never prevents the others from running.
"""

import asyncio
import logging
from typing import Type

from app.services.scrapers.base_scraper import BaseScraper
from app.services.scrapers.il_scraper import ILScraper
from app.services.scrapers.oh_scraper import OHScraper
from app.services.scrapers.tx_scraper import TXScraper

logger = logging.getLogger(__name__)

# Map state code → scraper class
SCRAPER_REGISTRY: dict[str, Type[BaseScraper]] = {
    "IL": ILScraper,
    "TX": TXScraper,
    "OH": OHScraper,
}


async def run_all_scrapers(states: list[str] | None = None) -> list[dict]:
    """Instantiate and run every registered scraper concurrently.

    Args:
        states: Optional subset of state codes to run.  Defaults to all keys
                in SCRAPER_REGISTRY.

    Returns:
        Flat list of normalised price records (same schema as price_snapshots).
    """
    target = states or list(SCRAPER_REGISTRY.keys())

    async def _run_one(state: str) -> list[dict]:
        scraper_cls = SCRAPER_REGISTRY.get(state)
        if scraper_cls is None:
            logger.warning("No scraper registered for state '%s' — skipping", state)
            return []
        try:
            scraper = scraper_cls()
            records = await scraper.scrape()
            logger.info("%-2s scraper finished: %d record(s) returned", state, len(records))
            return records
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "%-2s scraper raised an unhandled exception: %s", state, exc, exc_info=True
            )
            return []

    results = await asyncio.gather(*[_run_one(s) for s in target])
    combined: list[dict] = []
    for chunk in results:
        combined.extend(chunk)
    return combined
