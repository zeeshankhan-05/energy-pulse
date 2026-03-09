"""Data ingestion: pull EIA prices and persist via the normalization pipeline."""

import logging

from sqlalchemy.orm import Session

from app.services.eia_client import DEFAULT_STATES, EIAClient
from app.services.normalization import normalize_pipeline

logger = logging.getLogger(__name__)

# Source identifier stamped on every row ingested from the EIA API
_EIA_SOURCE = "EIA"


def ingest_eia_data(
    db: Session,
    states: list[str] | None = None,
    client: EIAClient | None = None,
    months: int = 24,
) -> int:
    """Fetch EIA prices for *states* and persist them via normalize_pipeline.

    *months* is passed to the EIA client as the ``length`` query parameter,
    controlling how many monthly records are fetched per state/fuel-type.
    Defaults to 24 (two years) for scheduled runs; pass a smaller value (e.g. 2)
    for quick seed runs.

    EIAClient returns records with keys: period, state, price, units, fuel_type.
    These are remapped to the pipeline schema (region, unit, source) before being
    fed into normalize_pipeline, which handles validation, unit/period normalization,
    deduplication, and bulk insert.

    Returns the count of **new** records inserted.
    """
    if client is None:
        client = EIAClient()
    if states is None:
        states = DEFAULT_STATES

    raw = client.fetch_all_states(states, length=months)

    # Remap EIAClient field names → pipeline schema field names
    pipeline_records: list[dict] = []
    for rec in raw:
        region = rec.get("state")
        period = rec.get("period")
        fuel_type = rec.get("fuel_type")

        if not (period and region and fuel_type):
            logger.warning("Skipping incomplete EIA record: %s", rec)
            continue

        pipeline_records.append({
            "source": _EIA_SOURCE,
            "region": region,
            "fuel_type": fuel_type,
            "price": rec.get("price"),
            "unit": rec.get("units", ""),
            "period": period,
            "raw_data": None,
        })

    if not pipeline_records:
        logger.info("ingest_eia_data: no records to process")
        return 0

    summary = normalize_pipeline(pipeline_records, db)
    logger.info(
        "ingest_eia_data: total=%d inserted=%d rejected=%d duplicates=%d",
        summary["total_received"],
        summary["inserted"],
        summary["rejected"],
        summary["duplicates"],
    )
    return summary["inserted"]
