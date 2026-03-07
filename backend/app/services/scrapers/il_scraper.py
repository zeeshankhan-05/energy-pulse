"""Illinois Commerce Commission (ICC) electricity rate scraper.

Target: https://www.icc.illinois.gov/electric/ElectricRates

The ICC electric rates page lists current residential tariff information for
Illinois utilities (ComEd, Ameren Illinois, etc.).  The page is server-rendered
HTML so Playwright is used mainly to handle any JS-injected content and to
bypass potential bot-detection; httpx is used as the fallback.

BLOCKING NOTE:
  icc.illinois.gov does not actively block headless browsers as of testing,
  but the page occasionally loads slowly.  No 403 issues observed.
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from app.services.scrapers.base_scraper import BaseScraper

_SOURCE = "IL_ICC"
_REGION = "IL"
_FUEL_TYPE = "electricity"
_URL = "https://www.icc.illinois.gov/electric/ElectricRates"

# Regex that matches a dollar-per-kWh value like "$0.1234" or "0.1234 $/kWh"
# and a cents-per-kWh value like "12.34¢" or "12.34 cents"
_RATE_RE = re.compile(
    r"""
    (?:
        \$\s*(?P<dollar>0\.\d{2,5})           # $0.XXXXX  → $/kWh
    |
        (?P<cents>\d{1,3}(?:\.\d{1,4})?)\s*   # XX.XX     → cents/kWh
        (?:¢|cents?|c/kWh|¢/kWh)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


class ILScraper(BaseScraper):
    """Scrape residential electricity rates from the Illinois Commerce Commission."""

    def scrape(self) -> list[dict]:
        """Return ICC rate records, or [] with a warning if the page is unreachable."""
        def _playwright_attempt() -> list[dict]:
            with self._browser_context() as ctx:
                page = ctx.new_page()
                self.logger.info("Loading %s", _URL)
                page.goto(_URL, wait_until="domcontentloaded", timeout=30_000)
                # Give any lazy-loaded tables a moment to appear
                try:
                    page.wait_for_selector("table", timeout=8_000)
                except Exception:
                    pass
                html = page.content()
            return self._parse_html(html)

        try:
            records = self.run_with_retry(_playwright_attempt, retries=2, delay=3)
        except Exception as exc:
            self.logger.warning(
                "Playwright failed for IL ICC (%s); trying httpx fallback", exc
            )
            records = self._httpx_scrape()

        self.logger.info("IL ICC → %d records", len(records))
        return records

    # ------------------------------------------------------------------
    # httpx fallback
    # ------------------------------------------------------------------

    def _httpx_scrape(self) -> list[dict]:
        try:
            resp = self._httpx_fallback(_URL)
            resp.raise_for_status()
            return self._parse_html(resp.text)
        except Exception as exc:
            self.logger.warning("IL ICC httpx fallback also failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    def _parse_html(self, html: str) -> list[dict]:
        """Extract utility rate records from the page HTML."""
        soup = BeautifulSoup(html, "lxml")
        records: list[dict] = []

        # Strategy 1: look for a structured <table> containing rate data
        records = self._parse_rate_tables(soup)
        if records:
            return records

        # Strategy 2: scan all text for rate-like numbers with utility mentions
        records = self._parse_rate_text(soup)
        if records:
            return records

        self.logger.warning(
            "IL ICC: no rate data found in page — structure may have changed"
        )
        return []

    def _parse_rate_tables(self, soup: BeautifulSoup) -> list[dict]:
        """Try every <table> on the page; return records from the first useful one."""
        period = datetime.now().strftime("%Y-%m")
        records: list[dict] = []

        for table in soup.find_all("table"):
            headers = [
                th.get_text(strip=True).lower()
                for th in table.find_all(["th", "td"])
                if th.get_text(strip=True)
            ]
            # A useful table will mention rates, utilities, or schedules
            if not any(
                kw in " ".join(headers)
                for kw in ("rate", "utility", "schedule", "price", "electric", "kwh")
            ):
                continue

            rows = table.find_all("tr")
            col_headers: list[str] = []
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
                if not cells:
                    continue

                if not col_headers or any(
                    kw in " ".join(cells).lower()
                    for kw in ("utility", "rate", "schedule", "effective")
                ):
                    col_headers = [c.lower() for c in cells]
                    continue

                # Try to extract a price from any cell
                price, unit = self._extract_rate_from_cells(cells)
                if price is None:
                    continue

                # Try to find a date cell
                effective_date = self._find_date_in_cells(cells)
                if effective_date:
                    period = self._parse_period(effective_date)

                utility = cells[0] if cells else "Unknown"
                records.append(
                    self.normalize_price_record(
                        source=_SOURCE,
                        region=_REGION,
                        fuel_type=_FUEL_TYPE,
                        price=round(price, 4),
                        unit=unit,
                        period=period,
                        raw={"utility": utility, "cells": cells, "url": _URL},
                    )
                )

        return records

    def _parse_rate_text(self, soup: BeautifulSoup) -> list[dict]:
        """Fall back to scanning all body text for rate-like values."""
        period = datetime.now().strftime("%Y-%m")
        text = soup.get_text(separator=" ")
        matches = list(_RATE_RE.finditer(text))
        if not matches:
            return []

        records: list[dict] = []
        seen: set[float] = set()
        for m in matches:
            if m.group("dollar"):
                price = float(m.group("dollar"))
                unit = "$/kWh"
            else:
                price = float(m.group("cents"))
                unit = "cents/kWh"

            price = round(price, 4)
            if price in seen or price < 0.01:
                continue
            seen.add(price)

            # Grab a small surrounding snippet as context
            start = max(0, m.start() - 60)
            snippet = text[start: m.end() + 60].strip()
            records.append(
                self.normalize_price_record(
                    source=_SOURCE,
                    region=_REGION,
                    fuel_type=_FUEL_TYPE,
                    price=price,
                    unit=unit,
                    period=period,
                    raw={"snippet": snippet, "url": _URL},
                )
            )

        return records

    # ------------------------------------------------------------------
    # Cell-level helpers
    # ------------------------------------------------------------------

    def _extract_rate_from_cells(self, cells: list[str]) -> tuple[float | None, str]:
        for cell in cells:
            m = _RATE_RE.search(cell)
            if m:
                if m.group("dollar"):
                    return float(m.group("dollar")), "$/kWh"
                return float(m.group("cents")), "cents/kWh"
        return None, ""

    _DATE_RE = re.compile(
        r"\b(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s*\d{4})\b",
        re.IGNORECASE,
    )

    def _find_date_in_cells(self, cells: list[str]) -> str | None:
        for cell in cells:
            m = self._DATE_RE.search(cell)
            if m:
                return m.group(1)
        return None
