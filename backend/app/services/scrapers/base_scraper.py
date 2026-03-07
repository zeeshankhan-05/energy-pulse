"""Abstract base class for all EnergyPulse web scrapers.

Every state scraper inherits:
  - _browser_context()  – shared Playwright Chromium context manager
  - _httpx_fallback()   – realistic-header HTTP fetch used when Playwright is
                          blocked (403 / Cloudflare / JS-challenge sites)
  - normalize_price_record() – guarantees the returned dict always matches the
                               price_snapshots DB schema exactly
  - run_with_retry()    – 3-attempt exponential backoff wrapper
"""

import abc
import logging
import random
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable

import httpx
from playwright.sync_api import BrowserContext, sync_playwright

# ---------------------------------------------------------------------------
# Rotating user agents (5 real Chrome desktop strings)
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

_CHROMIUM_ARGS: list[str] = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]


class BaseScraper(abc.ABC):
    """All state scrapers extend this class."""

    def __init__(self) -> None:
        # Logger name == concrete subclass name (ILScraper, TXScraper, …)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._user_agent: str = random.choice(USER_AGENTS)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def scrape(self) -> list[dict]:
        """Scrape target site and return a list of normalized price records."""

    # ------------------------------------------------------------------
    # Shared Playwright context manager
    # ------------------------------------------------------------------

    @contextmanager
    def _browser_context(self):
        """Yield a Playwright BrowserContext using a headless Chromium instance."""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=_CHROMIUM_ARGS,
            )
            context: BrowserContext = browser.new_context(
                user_agent=self._user_agent,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            try:
                yield context
            finally:
                context.close()
                browser.close()

    # ------------------------------------------------------------------
    # httpx fallback (used when Playwright is blocked)
    # ------------------------------------------------------------------

    def _httpx_fallback(self, url: str, **kwargs: Any) -> httpx.Response:
        """Fetch *url* with httpx using realistic browser-like headers.

        Use this when the target site returns 403 to Playwright or applies
        a JS challenge that can't be solved headlessly.
        """
        headers = {
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
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            return client.get(url, headers=headers, **kwargs)

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
        """Return a dict that matches the price_snapshots table schema exactly.

        Args:
            source:    Data publisher, e.g. "IL_ICC", "TX_PUCT"
            region:    Two-letter state code, e.g. "IL"
            fuel_type: "electricity" or "natural_gas"
            price:     Numeric rate value, or None if unavailable
            unit:      Human-readable unit, e.g. "cents/kWh", "$/MMBtu"
            period:    Month string in "YYYY-MM" format
            raw:       Any serialisable dict/list for the JSONB raw_data column
        """
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
    # Retry wrapper
    # ------------------------------------------------------------------

    def run_with_retry(
        self,
        fn: Callable[[], Any],
        retries: int = 3,
        delay: int = 5,
    ) -> Any:
        """Call *fn* up to *retries* times with exponential back-off.

        Back-off schedule: delay * 2^attempt seconds between attempts.
        Raises the last exception after all attempts are exhausted.
        """
        last_exc: Exception = RuntimeError("No attempts made")
        for attempt in range(retries):
            try:
                return fn()
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
                    time.sleep(backoff)
                else:
                    self.logger.error(
                        "All %d attempts exhausted: %s",
                        retries,
                        exc,
                    )
        raise last_exc

    # ------------------------------------------------------------------
    # Internal utility
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_period(date_str: str) -> str:
        """Convert a date string to 'YYYY-MM' format.

        Tries common US date formats; falls back to the current month.
        """
        for fmt in (
            "%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y",
            "%B %Y",    "%b %Y",    "%m-%d-%Y",   "%d/%m/%Y",
        ):
            try:
                return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m")
            except ValueError:
                continue
        return datetime.now().strftime("%Y-%m")
