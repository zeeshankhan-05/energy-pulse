"""Illinois Commerce Commission (ICC) electricity rate scraper.

Primary target: https://www.icc.illinois.gov/electric/ElectricRates
EIA fallback:   EIA API v2 residential electricity data for IL

BLOCKING NOTE:
  icc.illinois.gov/electric/ currently returns HTTP 500 (ASP.NET runtime error)
  on all sub-pages.  The httpx attempt is kept so it auto-recovers if the site
  comes back up.  When it fails the EIA API is used as the authoritative source.
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from app.services.scrapers.base_scraper import BaseScraper

_SOURCE = "IL_ICC"
_EIA_SOURCE = "IL_ICC_EIA_FALLBACK"
_REGION = "IL"
_FUEL_TYPE = "electricity"
_URL = "https://www.icc.illinois.gov/electric/ElectricRates"

_RATE_RE = re.compile(
    r"""
    (?:
        \$\s*(?P<dollar>0\.\d{2,5})
    |
        (?P<cents>\d{1,3}(?:\.\d{1,4})?)\s*
        (?:¢|cents?|c/kWh|¢/kWh)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


class ILScraper(BaseScraper):
    """Scrape residential electricity rates from the Illinois Commerce Commission."""

    async def scrape(self) -> list[dict]:
        """Try ICC site; fall back to EIA API if the primary source is down."""
        try:
            html = await self._fetch_html(_URL)
            records = self._parse_html(html)
            if records:
                self.logger.info("IL ICC (live) → %d records", len(records))
                return records
            self.logger.warning("IL ICC: page loaded but no rate data found")
        except Exception as exc:
            self.logger.warning("IL ICC fetch failed (%s); using EIA fallback", exc)

        records = await self._fetch_eia_electricity(_REGION, _EIA_SOURCE)
        self.logger.info("IL ICC (EIA fallback) → %d records", len(records))
        return records

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    def _parse_html(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        records = self._parse_rate_tables(soup)
        if records:
            return records
        return self._parse_rate_text(soup)

    def _parse_rate_tables(self, soup: BeautifulSoup) -> list[dict]:
        period = datetime.now().strftime("%Y-%m")
        records: list[dict] = []

        for table in soup.find_all("table"):
            headers_text = " ".join(
                th.get_text(strip=True).lower() for th in table.find_all(["th", "td"])
            )
            if not any(kw in headers_text for kw in ("rate", "utility", "schedule", "price", "electric", "kwh")):
                continue

            for row in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
                if len(cells) < 2:
                    continue
                price, unit = self._extract_rate(cells)
                if price is None:
                    continue
                utility = cells[0]
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
        period = datetime.now().strftime("%Y-%m")
        text = soup.get_text(separator=" ")
        records: list[dict] = []
        seen: set[float] = set()

        for m in _RATE_RE.finditer(text):
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

    def _extract_rate(self, cells: list[str]) -> tuple[float | None, str]:
        for cell in cells:
            m = _RATE_RE.search(cell)
            if m:
                if m.group("dollar"):
                    return float(m.group("dollar")), "$/kWh"
                return float(m.group("cents")), "cents/kWh"
        return None, ""
