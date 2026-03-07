"""Data ingestion: pull EIA prices and upsert into price_snapshots."""

import logging

from sqlalchemy.orm import Session

from app.models.price_snapshot import PriceSnapshot
from app.services.eia_client import DEFAULT_STATES, EIAClient

logger = logging.getLogger(__name__)

# Source identifier stamped on every row ingested from the EIA API
_EIA_SOURCE = "EIA"


def ingest_eia_data(
    db: Session,
    states: list[str] | None = None,
    client: EIAClient | None = None,
) -> int:
    """Fetch EIA prices for *states* and upsert into price_snapshots.

    EIAClient returns records with keys: period, state, price, units, fuel_type.
    These are mapped to the PriceSnapshot schema (region, unit, source, raw_data).

    Returns the count of **new** records inserted; existing rows (matched on
    source + region + fuel_type + period) are silently skipped.
    """
    if client is None:
        client = EIAClient()
    if states is None:
        states = DEFAULT_STATES

    records = client.fetch_all_states(states)
    new_count = 0

    for rec in records:
        period = rec.get("period")
        region = rec.get("state")        # EIAClient uses "state" key
        fuel_type = rec.get("fuel_type")

        if not (period and region and fuel_type):
            logger.warning("Skipping incomplete record: %s", rec)
            continue

        exists = (
            db.query(PriceSnapshot)
            .filter_by(
                source=_EIA_SOURCE,
                region=region,
                fuel_type=fuel_type,
                period=period,
            )
            .first()
        )
        if exists:
            continue

        db.add(
            PriceSnapshot(
                source=_EIA_SOURCE,
                region=region,
                fuel_type=fuel_type,
                period=period,
                price=rec.get("price"),
                unit=rec.get("units", ""),  # EIAClient returns "units" key
                raw_data=None,              # raw blob not captured at this layer
            )
        )
        new_count += 1

    db.commit()
    logger.info("ingest_eia_data: inserted %d new records", new_count)
    return new_count
