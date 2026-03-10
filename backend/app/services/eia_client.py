"""EIA API v2 client with retry logic and response normalisation."""

import logging
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_ELECTRICITY_URL = "https://api.eia.gov/v2/electricity/retail-sales/data/"
_NATURAL_GAS_URL = "https://api.eia.gov/v2/natural-gas/pri/sum/data/"

DEFAULT_STATES = ["IL", "TX", "OH", "CA", "NY", "AZ"]


class EIAClient:
    """Thin client around EIA Open Data API v2."""

    def __init__(self, http_client: httpx.Client | None = None, max_retries: int = 3) -> None:
        self.api_key = settings.eia_api_key
        self.max_retries = max_retries
        # Accept an injected client so tests can pass a mock without patching.
        self._client = http_client or httpx.Client(timeout=30.0)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_electricity_retail_prices(self, state_code: str, length: int = 24) -> list[dict]:
        """Return up to *length* months of residential electricity retail prices for *state_code*.

        Each dict has keys: period, state, price (float | None), units.
        """
        params = [
            ("api_key", self.api_key),
            ("frequency", "monthly"),
            ("data[]", "price"),
            ("facets[sectorid][]", "RES"),
            ("facets[stateid][]", state_code.upper()),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "desc"),
            ("length", str(length)),
        ]
        raw = self._request_with_retry(_ELECTRICITY_URL, params, label=f"electricity/{state_code}")
        data: list[dict] = raw.get("response", {}).get("data", [])
        logger.info("electricity/%s returned %d records", state_code, len(data))
        return [
            {
                "period": item["period"],
                "state": item.get("stateid", state_code).upper(),
                "price": _to_float(item.get("price")),
                "units": item.get("price-units", ""),
            }
            for item in data
        ]

    def get_natural_gas_prices(self, state_code: str, length: int = 24) -> list[dict]:
        """Return up to *length* months of residential natural gas prices for *state_code*.

        EIA uses ``duoarea`` codes of the form ``S{STATE}`` (e.g. ``SIL``).
        Each dict has keys: period, state, price (float | None), units.
        """
        duoarea = f"S{state_code.upper()}"
        params = [
            ("api_key", self.api_key),
            ("frequency", "monthly"),
            ("data[]", "value"),
            ("facets[duoarea][]", duoarea),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "desc"),
            ("length", str(length)),
        ]
        raw = self._request_with_retry(_NATURAL_GAS_URL, params, label=f"natural_gas/{state_code}")
        data: list[dict] = raw.get("response", {}).get("data", [])
        logger.info("natural_gas/%s returned %d records", state_code, len(data))
        return [
            {
                "period": item["period"],
                # duoarea "SIL" -> state "IL"
                "state": item.get("duoarea", duoarea)[1:].upper(),
                "price": _to_float(item.get("value")),
                "units": item.get("units", ""),
            }
            for item in data
        ]

    def fetch_all_states(self, states: list[str] | None = None, length: int = 24) -> list[dict]:
        """Fetch electricity + natural gas prices for every state in *states*.

        *length* controls how many monthly records are requested from the EIA API
        per state/fuel-type combination (passed directly as the ``length`` query param).

        Returns a combined, normalised list.  Each record gains a ``fuel_type``
        field set to ``"electricity"`` or ``"natural_gas"``.
        """
        if states is None:
            states = DEFAULT_STATES

        combined: list[dict] = []
        for state in states:
            for fuel_type, fetcher in (
                ("electricity", self.get_electricity_retail_prices),
                ("natural_gas", self.get_natural_gas_prices),
            ):
                try:
                    records = fetcher(state, length=length)
                    for rec in records:
                        rec["fuel_type"] = fuel_type
                    combined.extend(records)
                except httpx.HTTPError as exc:
                    logger.error("Failed to fetch %s/%s: %s", fuel_type, state, exc)

        return combined

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request_with_retry(
        self, url: str, params: list[tuple[str, Any]], label: str
    ) -> dict:
        last_exc: Exception = RuntimeError("No attempts made")
        for attempt in range(self.max_retries):
            try:
                logger.debug("GET %s [%s] attempt %d", url, label, attempt + 1)
                response = self._client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    backoff = 2**attempt
                    logger.warning(
                        "Request %s failed (attempt %d/%d): %s — retrying in %ds",
                        label,
                        attempt + 1,
                        self.max_retries,
                        exc,
                        backoff,
                    )
                    time.sleep(backoff)
        raise last_exc


def _to_float(value: Any) -> float | None:
    """Safely coerce EIA string values to float."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
