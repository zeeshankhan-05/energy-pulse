"""Ohio PUCO electricity rate scraper.

Primary target:
  https://puco.ohio.gov/utilities/electricity/resources/
  electric-security-plans-esps-and-market-rate-options-mros
EIA fallback: EIA API v2 residential electricity data for OH

BLOCKING NOTE:
  puco.ohio.gov uses IBM WebSphere Portal; page content is rendered client-side
  via JavaScript.  httpx retrieves only navigation chrome (no rate data).
  The fetch is attempted so it auto-recovers if a static fallback URL becomes
  available.  EIA API is used as the authoritative fallback.
"""

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.services.scrapers.base_scraper import BaseScraper

_SOURCE = "OH_PUCO"
_EIA_SOURCE = "OH_PUCO_EIA_FALLBACK"
_REGION = "OH"
_FUEL_TYPE = "electricity"
_BASE_URL = "https://puco.ohio.gov"
_URL = (
    "https://puco.ohio.gov/utilities/electricity/resources/"
    "electric-security-plans-esps-and-market-rate-options-mros"
)

_RATE_RE = re.compile(
    r"""
    (?:
        \$\s*(?P<dollar>0\.\d{2,5})
    |
        (?P<cents>\d{1,3}(?:\.\d{1,4})?)
        \s*(?:¢|cents?|c/kWh)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


class OHScraper(BaseScraper):
    """Scrape electricity rate data from Ohio PUCO ESP/MRO page."""

    async def scrape(self) -> list[dict]:
        """Try PUCO page; fall back to EIA API if no inline rates are found."""
        try:
            html = await self._fetch_html(_URL)
            records = self._parse_html(html)
            if records:
                self.logger.info("OH PUCO (live) → %d records", len(records))
                return records
            self.logger.warning(
                "OH PUCO: page loaded but no inline rate data (JS-rendered); "
                "using EIA fallback"
            )
        except Exception as exc:
            self.logger.warning("OH PUCO fetch failed (%s); using EIA fallback", exc)

        records = await self._fetch_eia_electricity(_REGION, _EIA_SOURCE)
        self.logger.info("OH PUCO (EIA fallback) → %d records", len(records))
        return records

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    def _parse_html(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        period = datetime.now().strftime("%Y-%m")

        records = self._parse_rate_tables(soup, period)
        if records:
            return records

        pdf_links = self._collect_document_links(soup)
        rate_records = self._parse_rate_text(soup, period)
        if rate_records:
            for rec in rate_records:
                if rec.get("raw_data"):
                    rec["raw_data"]["pdf_links"] = pdf_links[:10]
            return rate_records

        if pdf_links:
            self.logger.info(
                "OH PUCO: found %d document links but no inline rates; "
                "returning placeholder record",
                len(pdf_links),
            )
            return [
                self.normalize_price_record(
                    source=_SOURCE,
                    region=_REGION,
                    fuel_type=_FUEL_TYPE,
                    price=None,
                    unit="cents/kWh",
                    period=period,
                    raw={
                        "note": "Rates embedded in linked PDFs; manual extraction needed",
                        "pdf_links": pdf_links[:15],
                        "url": _URL,
                    },
                )
            ]

        return []

    def _parse_rate_tables(self, soup: BeautifulSoup, period: str) -> list[dict]:
        records: list[dict] = []
        for table in soup.find_all("table"):
            text = table.get_text(separator=" ").lower()
            if not any(kw in text for kw in ("rate", "kwh", "cent", "price", "electric")):
                continue
            for row in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
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

    def _parse_rate_text(self, soup: BeautifulSoup, period: str) -> list[dict]:
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
            start = max(0, m.start() - 80)
            snippet = text[start: m.end() + 80].strip()
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

    def _collect_document_links(self, soup: BeautifulSoup) -> list[str]:
        links: list[str] = []
        for a in soup.find_all("a", href=True):
            href: str = a["href"]
            if any(ext in href.lower() for ext in (".pdf", ".doc", ".docx", ".xlsx")):
                links.append(urljoin(_BASE_URL, href))
        return links

    def _extract_rate(self, cells: list[str]) -> tuple[float | None, str]:
        for cell in cells:
            m = _RATE_RE.search(cell)
            if m:
                if m.group("dollar"):
                    return float(m.group("dollar")), "$/kWh"
                return float(m.group("cents")), "cents/kWh"
        return None, ""
