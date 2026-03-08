"""Texas ERCOT / PUCT electricity rate scraper.

Primary targets (tried in order):
  1. https://www.ercot.com/api/1/services/read/dashboards/retail-electric-provider-switch-report.json
  2. https://www.powertochoose.org/api/public/plans  (internal JSON API)
EIA fallback: EIA API v2 residential electricity data for TX

BLOCKING NOTE:
  The ERCOT dashboard JSON endpoint currently returns 404 (API migrated).
  Power to Choose uses Cloudflare protection that blocks httpx.
  EIA API is used as the authoritative fallback source.
"""

import re
from datetime import datetime

from app.services.scrapers.base_scraper import BaseScraper

_SOURCE = "TX_ERCOT"
_EIA_SOURCE = "TX_ERCOT_EIA_FALLBACK"
_REGION = "TX"
_FUEL_TYPE = "electricity"

_ERCOT_JSON_URL = (
    "https://www.ercot.com/api/1/services/read/dashboards/"
    "retail-electric-provider-switch-report.json"
)
_PTC_API_URL = "https://www.powertochoose.org/api/public/plans"
_PTC_PARAMS = {
    "language": "en",
    "zip_code": "77002",
    "renewable": "false",
    "page": "1",
    "pageSize": "25",
}

_RATE_RE = re.compile(
    r"(\d{1,3}(?:\.\d{1,3})?)\s*(?:¢|cents?|c/kWh|¢/kWh|cent\s?per)",
    re.IGNORECASE,
)
_DOLLAR_RATE_RE = re.compile(r"\$\s*(0\.\d{3,5})\s*/\s*kWh", re.IGNORECASE)


class TXScraper(BaseScraper):
    """Scrape average residential electricity rate for Texas."""

    async def scrape(self) -> list[dict]:
        # 1. Try ERCOT JSON endpoint
        try:
            data = await self._fetch_json(_ERCOT_JSON_URL)
            records = self._parse_ercot_json(data)
            if records:
                self.logger.info("TX ERCOT (JSON) → %d records", len(records))
                return records
        except Exception as exc:
            self.logger.warning("TX ERCOT JSON failed (%s)", exc)

        # 2. Try Power to Choose internal API
        try:
            data = await self._fetch_json(_PTC_API_URL, params=_PTC_PARAMS)
            records = self._parse_ptc_json(data)
            if records:
                self.logger.info("TX Power to Choose (API) → %d records", len(records))
                return records
        except Exception as exc:
            self.logger.warning("TX Power to Choose API failed (%s)", exc)

        # 3. EIA fallback
        records = await self._fetch_eia_electricity(_REGION, _EIA_SOURCE)
        self.logger.info("TX ERCOT (EIA fallback) → %d records", len(records))
        return records

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_ercot_json(self, data: dict | list) -> list[dict]:
        """Parse ERCOT dashboard JSON for average residential rates."""
        # The structure varies by endpoint version; try common shapes
        if isinstance(data, list):
            rows = data
        else:
            rows = (
                data.get("data")
                or data.get("rows")
                or data.get("records")
                or []
            )

        rates: list[float] = []
        for row in rows[:50]:
            if not isinstance(row, dict):
                continue
            val = (
                row.get("averagePrice")
                or row.get("avgPrice")
                or row.get("price")
                or row.get("rate")
            )
            if val is None:
                continue
            try:
                rate = float(val)
            except (TypeError, ValueError):
                continue
            if rate > 1:  # cents/kWh
                pass
            else:
                rate *= 100  # convert $/kWh
            if 1.0 < rate < 100.0:
                rates.append(rate)

        return self._build_record(rates, method="ercot_json")

    def _parse_ptc_json(self, data: dict | list) -> list[dict]:
        """Parse Power to Choose JSON for plan rates."""
        plans = data if isinstance(data, list) else data.get("plans", data.get("data", []))
        rates: list[float] = []
        for plan in plans[:25]:
            if not isinstance(plan, dict):
                continue
            val = (
                plan.get("price")
                or plan.get("rate")
                or plan.get("kwh500Price")
                or plan.get("price500kWh")
            )
            if val is None:
                continue
            try:
                rate = float(val)
            except (TypeError, ValueError):
                continue
            if rate <= 1:
                rate *= 100
            if 1.0 < rate < 100.0:
                rates.append(rate)

        return self._build_record(rates, method="ptc_json_api")

    def _build_record(self, rates: list[float], method: str) -> list[dict]:
        if not rates:
            return []
        avg = round(sum(rates) / len(rates), 4)
        period = datetime.now().strftime("%Y-%m")
        return [
            self.normalize_price_record(
                source=_SOURCE,
                region=_REGION,
                fuel_type=_FUEL_TYPE,
                price=avg,
                unit="cents/kWh",
                period=period,
                raw={
                    "method": method,
                    "plan_count": len(rates),
                    "avg_rate_cents_kwh": avg,
                    "min": round(min(rates), 4),
                    "max": round(max(rates), 4),
                },
            )
        ]
