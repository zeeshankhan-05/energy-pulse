"""Texas PUCT / Power to Choose electricity plan scraper.

Target: https://www.powertochoose.org/en-us/Plan/Results

Power to Choose is the Texas PUCT competitive electricity marketplace.
It is a React SPA that loads plans via an internal JSON API.

BLOCKING NOTE:
  powertochoose.org uses Cloudflare bot protection and returns a JS-challenge
  page to headless Chromium.  Playwright alone is insufficient; even with a
  realistic user-agent the challenge page is served.

FALLBACK STRATEGY:
  1. Attempt Playwright with a 15-second wait for plan cards.
  2. If no cards found (challenge page served), fall back to the site's
     internal JSON API endpoint:
       GET https://www.powertochoose.org/api/public/plans
     which is more permissive and returns structured plan data.
  3. If the API also fails, return [] with a warning.
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from app.services.scrapers.base_scraper import BaseScraper

_SOURCE = "TX_PUCT"
_REGION = "TX"
_FUEL_TYPE = "electricity"
_URL = "https://www.powertochoose.org/en-us/Plan/Results"

# Power to Choose internal API – returns JSON plan data without a JS challenge
_API_URL = "https://www.powertochoose.org/api/public/plans"

# Regex for cents-per-kWh values like "12.3¢", "9.5 cents/kWh"
_RATE_RE = re.compile(
    r"(\d{1,3}(?:\.\d{1,3})?)\s*(?:¢|cents?|c/kWh|¢/kWh|cent\s?per)",
    re.IGNORECASE,
)
# Regex for dollar values like "$0.093/kWh"
_DOLLAR_RATE_RE = re.compile(
    r"\$\s*(0\.\d{3,5})\s*/\s*kWh",
    re.IGNORECASE,
)


class TXScraper(BaseScraper):
    """Scrape the average residential electricity rate from Power to Choose Texas."""

    def scrape(self) -> list[dict]:
        # 1. Try Playwright
        try:
            result = self.run_with_retry(self._playwright_scrape, retries=2, delay=3)
            if result:
                self.logger.info("TX PUCT (Playwright) → %d records", len(result))
                return result
        except Exception as exc:
            self.logger.warning("TX Playwright attempt failed: %s", exc)

        # 2. Try internal JSON API
        try:
            result = self._api_scrape()
            if result:
                self.logger.info("TX PUCT (API) → %d records", len(result))
                return result
        except Exception as exc:
            self.logger.warning("TX API fallback failed: %s", exc)

        # 3. Return empty with explanation
        self.logger.warning(
            "TX PUCT: all scraping methods failed — "
            "powertochoose.org Cloudflare protection active"
        )
        return []

    # ------------------------------------------------------------------
    # Playwright attempt
    # ------------------------------------------------------------------

    def _playwright_scrape(self) -> list[dict]:
        with self._browser_context() as ctx:
            page = ctx.new_page()
            self.logger.info("Loading %s", _URL)
            page.goto(_URL, wait_until="domcontentloaded", timeout=30_000)

            # Wait for plan cards; Cloudflare challenge will not have these
            try:
                page.wait_for_selector(
                    "[class*='plan'], [class*='Plan'], [data-plan], .card",
                    timeout=15_000,
                )
            except Exception:
                # Likely Cloudflare JS challenge – return empty to trigger fallback
                self.logger.debug("TX: no plan cards found within timeout")
                return []

            html = page.content()

        return self._parse_plan_html(html)

    # ------------------------------------------------------------------
    # JSON API fallback
    # ------------------------------------------------------------------

    def _api_scrape(self) -> list[dict]:
        """Hit Power to Choose's internal JSON API for plan data."""
        import httpx  # local import for clarity

        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.powertochoose.org/",
            "Origin": "https://www.powertochoose.org",
        }

        # Query params that the SPA sends when loading the default results page
        params = {
            "language": "en",
            "zip_code": "77002",  # Houston ZIP — representative TX residential
            "renewable": "false",
            "page": "1",
            "pageSize": "25",
        }

        with httpx.Client(follow_redirects=True, timeout=20.0) as client:
            resp = client.get(_API_URL, headers=headers, params=params)

        if resp.status_code != 200:
            raise RuntimeError(f"TX API returned {resp.status_code}")

        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"TX API non-JSON response: {exc}") from exc

        return self._parse_plan_json(data)

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_plan_html(self, html: str) -> list[dict]:
        """Extract plan rates from the rendered React HTML."""
        soup = BeautifulSoup(html, "lxml")
        rates: list[float] = []
        raw_plans: list[dict] = []

        for card in soup.select("[class*='plan'], [class*='Plan'], [class*='card']")[:20]:
            text = card.get_text(separator=" ")
            rate = self._extract_rate(text)
            if rate is not None:
                rates.append(rate)
                raw_plans.append({"text": text[:200], "rate": rate})

        return self._build_record(rates, raw_plans, method="playwright_html")

    def _parse_plan_json(self, data: dict | list) -> list[dict]:
        """Extract plan rates from the Power to Choose JSON response."""
        plans = data if isinstance(data, list) else data.get("plans", data.get("data", []))
        rates: list[float] = []
        raw_plans: list[dict] = []

        for plan in plans[:20]:
            # Field names vary; try common keys
            rate_val = (
                plan.get("price")
                or plan.get("rate")
                or plan.get("kwh500Price")
                or plan.get("price500kWh")
                or plan.get("baseCharge")
            )
            if rate_val is None:
                continue
            try:
                rate = float(rate_val)
            except (TypeError, ValueError):
                continue

            # Power to Choose rates are typically in cents/kWh
            if rate > 1:   # already in cents/kWh
                pass
            else:           # dollars/kWh — convert
                rate = rate * 100

            if 1.0 < rate < 100.0:
                rates.append(rate)
                raw_plans.append(
                    {
                        "provider": plan.get("provider") or plan.get("retailer"),
                        "plan": plan.get("planName") or plan.get("name"),
                        "rate_cents": rate,
                    }
                )

        return self._build_record(rates, raw_plans, method="json_api")

    def _build_record(
        self, rates: list[float], raw_plans: list[dict], method: str
    ) -> list[dict]:
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
                    "plans_sample": raw_plans[:5],
                    "url": _URL,
                },
            )
        ]

    def _extract_rate(self, text: str) -> float | None:
        """Pull the first recognisable rate value out of a text snippet."""
        m = _RATE_RE.search(text)
        if m:
            v = float(m.group(1))
            if 1.0 < v < 100.0:
                return v
        m = _DOLLAR_RATE_RE.search(text)
        if m:
            v = float(m.group(1)) * 100  # convert $/kWh → cents/kWh
            if 1.0 < v < 100.0:
                return v
        return None
