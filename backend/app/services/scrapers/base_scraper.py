"""Abstract base class for all EnergyPulse web scrapers.

Every state scraper inherits:
  - _fetch_html()    – async httpx.AsyncClient GET with realistic browser headers
  - _fetch_json()    – async httpx.AsyncClient GET returning parsed JSON
  - _fetch_eia_electricity() – EIA API fallback for residential state prices
  - normalize_price_record() – guarantees the returned dict matches
                               the price_snapshots DB schema exactly
  - run_with_retry() – async retry wrapper with exponential back-off
  - _parse_period()  – safe date-string → "YYYY-MM" converter

PLAYWRIGHT NOTE:
  Playwright Chromium is commented out in requirements.txt and Dockerfile.
  It works fine on Linux/EC2 servers; the browser download CDN blocks
  Mac ARM environments.  Uncomment those lines for production deployment.
  For local development and testing we use httpx + BeautifulSoup instead.
"""

import abc
import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Callable, Coroutine

import httpx

from app.config import settings

# ---------------------------------------------------------------------------
# Five rotating Chrome user-agent strings
# ---------------------------------------------------------------------------
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# EIA v2 residential electricity endpoint (proven working)
_EIA_ELEC_URL = "https://api.eia.gov/v2/electricity/retail-sales/data/"


class BaseScraper(abc.ABC):
    """All state scrapers extend this class."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._user_agent: str = random.choice(USER_AGENTS)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def scrape(self) -> list[dict]:
        """Scrape target site and return normalised price records."""

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _browser_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }

    async def _fetch_html(self, url: str, **kwargs: Any) -> str:
        """GET *url* with httpx.AsyncClient; returns response text."""
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=25.0
        ) as client:
            resp = await client.get(url, headers=self._browser_headers(), **kwargs)
            resp.raise_for_status()
            return resp.text

    async def _fetch_json(self, url: str, **kwargs: Any) -> Any:
        """GET *url* expecting a JSON body; returns parsed object."""
        headers = {
            **self._browser_headers(),
            "Accept": "application/json, text/plain, */*",
        }
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=25.0
        ) as client:
            resp = await client.get(url, headers=headers, **kwargs)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # EIA API fallback (used when primary government source is unavailable)
    # ------------------------------------------------------------------

    async def _fetch_eia_electricity(
        self, state_code: str, source_label: str, months: int = 12
    ) -> list[dict]:
        """Fetch monthly residential electricity prices from EIA API v2.

        Args:
            state_code:   Two-letter state code, e.g. "IL"
            source_label: Value to set in the 'source' field of each record
                          (e.g. "IL_ICC_EIA_FALLBACK")
            months:       Number of monthly records to fetch (default 12)

        Returns:
            List of normalised price records with source=*source_label*.
        """
        params = [
            ("api_key", settings.eia_api_key),
            ("frequency", "monthly"),
            ("data[]", "price"),
            ("facets[sectorid][]", "RES"),
            ("facets[stateid][]", state_code.upper()),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "desc"),
            ("length", str(months)),
        ]
        try:
            data = await self._fetch_json(_EIA_ELEC_URL, params=params)
        except httpx.HTTPStatusError as exc:
            self.logger.error("EIA API returned %s for %s", exc.response.status_code, state_code)
            return []

        rows = data.get("response", {}).get("data", [])
        self.logger.info(
            "EIA fallback for %s: %d records (source=%s)", state_code, len(rows), source_label
        )
        return [
            self.normalize_price_record(
                source=source_label,
                region=item.get("stateid", state_code).upper(),
                fuel_type="electricity",
                price=_to_float(item.get("price")),
                unit=item.get("price-units", "cents per kilowatt-hour"),
                period=item["period"],
                raw={
                    "via": "EIA_API_v2",
                    "note": "primary source unavailable; using EIA as fallback",
                    "sector": "residential",
                },
            )
            for item in rows
        ]

    # ------------------------------------------------------------------
    # Schema normalisation helper
    # ------------------------------------------------------------------

    def normalize_price_record(
        self,
        source: str,
        region: str,
        fuel_type: str,
        price: float | None,
        unit: str,
        period: str,
        raw: Any = None,
    ) -> dict:
        """Return a dict matching the price_snapshots table schema exactly."""
        return {
            "source": source,
            "region": region,
            "fuel_type": fuel_type,
            "price": price,
            "unit": unit,
            "period": period,
            "raw_data": raw,
        }

    # ------------------------------------------------------------------
    # Async retry wrapper
    # ------------------------------------------------------------------

    async def run_with_retry(
        self,
        coro_fn: Callable[[], Coroutine],
        retries: int = 3,
        delay: int = 5,
    ) -> Any:
        """Await *coro_fn()* up to *retries* times with exponential back-off.

        Back-off schedule: delay * 2^attempt seconds between retries.
        """
        last_exc: Exception = RuntimeError("No attempts made")
        for attempt in range(retries):
            try:
                return await coro_fn()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries - 1:
                    backoff = delay * (2**attempt)
                    self.logger.warning(
                        "Attempt %d/%d failed: %s — retrying in %ds",
                        attempt + 1,
                        retries,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    self.logger.error(
                        "All %d attempts exhausted: %s", retries, exc
                    )
        raise last_exc

    # ------------------------------------------------------------------
    # Date parsing utility
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_period(date_str: str) -> str:
        """Convert a date string to 'YYYY-MM' format; falls back to current month."""
        for fmt in (
            "%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y",
            "%B %Y",    "%b %Y",    "%m-%d-%Y",   "%d/%m/%Y",
        ):
            try:
                return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m")
            except ValueError:
                continue
        return datetime.now().strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
