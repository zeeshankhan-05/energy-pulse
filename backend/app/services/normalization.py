"""Data normalization pipeline — standardizes all incoming price records into
a consistent, query-ready format before they reach price_snapshots."""

import json
import logging
import re
from datetime import datetime, timezone

import redis as redis_lib
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.price_snapshot import PriceSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unit conversion table
# ---------------------------------------------------------------------------

UNIT_CONVERSIONS: dict[str, dict] = {
    # Short-form canonical keys
    "cents/kWh":              {"factor": 0.01,   "normalized_unit": "$/kWh"},
    "mills/kWh":              {"factor": 0.001,  "normalized_unit": "$/kWh"},
    "$/MWh":                  {"factor": 0.001,  "normalized_unit": "$/kWh"},
    "$/Mcf":                  {"factor": 1.02,   "normalized_unit": "$/MMBtu"},
    "$/therm":                {"factor": 10.0,   "normalized_unit": "$/MMBtu"},
    "$/MMBtu":                {"factor": 1.0,    "normalized_unit": "$/MMBtu"},
    "$/kWh":                  {"factor": 1.0,    "normalized_unit": "$/kWh"},
    # Long-form strings returned by the EIA API
    "cents per kilowatt-hour":   {"factor": 0.01,  "normalized_unit": "$/kWh"},
    "mills per kilowatt-hour":   {"factor": 0.001, "normalized_unit": "$/kWh"},
    "dollars per megawatthour":  {"factor": 0.001, "normalized_unit": "$/kWh"},
    "dollars per kilowatthour":  {"factor": 1.0,   "normalized_unit": "$/kWh"},
    "dollars per mcf":           {"factor": 1.02,  "normalized_unit": "$/MMBtu"},
    "dollars per therm":         {"factor": 10.0,  "normalized_unit": "$/MMBtu"},
    "dollars per mmbtu":         {"factor": 1.0,   "normalized_unit": "$/MMBtu"},
}

# ---------------------------------------------------------------------------
# State → full name map (all 50 US states)
# ---------------------------------------------------------------------------

STATE_REGION_MAP: dict[str, str] = {
    "AL": "Alabama",        "AK": "Alaska",         "AZ": "Arizona",
    "AR": "Arkansas",       "CA": "California",     "CO": "Colorado",
    "CT": "Connecticut",    "DE": "Delaware",        "FL": "Florida",
    "GA": "Georgia",        "HI": "Hawaii",          "ID": "Idaho",
    "IL": "Illinois",       "IN": "Indiana",         "IA": "Iowa",
    "KS": "Kansas",         "KY": "Kentucky",        "LA": "Louisiana",
    "ME": "Maine",          "MD": "Maryland",        "MA": "Massachusetts",
    "MI": "Michigan",       "MN": "Minnesota",       "MS": "Mississippi",
    "MO": "Missouri",       "MT": "Montana",         "NE": "Nebraska",
    "NV": "Nevada",         "NH": "New Hampshire",   "NJ": "New Jersey",
    "NM": "New Mexico",     "NY": "New York",        "NC": "North Carolina",
    "ND": "North Dakota",   "OH": "Ohio",            "OK": "Oklahoma",
    "OR": "Oregon",         "PA": "Pennsylvania",    "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota",    "TN": "Tennessee",
    "TX": "Texas",          "UT": "Utah",            "VT": "Vermont",
    "VA": "Virginia",       "WA": "Washington",      "WV": "West Virginia",
    "WI": "Wisconsin",      "WY": "Wyoming",
}

_QUARTER_TO_MONTH = {"Q1": "01", "Q2": "04", "Q3": "07", "Q4": "10"}

_MONTH_NAME_TO_NUM = {
    "january": "01",   "february": "02",  "march": "03",     "april": "04",
    "may": "05",       "june": "06",      "july": "07",      "august": "08",
    "september": "09", "october": "10",   "november": "11",  "december": "12",
    "jan": "01",       "feb": "02",       "mar": "03",       "apr": "04",
    "jun": "06",       "jul": "07",       "aug": "08",       "sep": "09",
    "oct": "10",       "nov": "11",       "dec": "12",
}


# ---------------------------------------------------------------------------
# normalize_unit
# ---------------------------------------------------------------------------

def normalize_unit(price: float, from_unit: str) -> tuple[float, str]:
    """Convert *price* from *from_unit* to the canonical normalized unit.

    Returns (converted_price, normalized_unit_string).
    Raises ValueError for unknown units (and logs a warning first).
    """
    conv = UNIT_CONVERSIONS.get(from_unit)
    if conv is None:
        # Try case-insensitive fallback
        for key, val in UNIT_CONVERSIONS.items():
            if key.lower() == from_unit.lower():
                conv = val
                break

    if conv is None:
        logger.warning("normalize_unit: unknown unit '%s'", from_unit)
        raise ValueError(f"Unknown unit: '{from_unit}'")

    converted = round(price * conv["factor"], 6)
    return converted, conv["normalized_unit"]


# ---------------------------------------------------------------------------
# normalize_period
# ---------------------------------------------------------------------------

def normalize_period(period_str: str) -> str:
    """Parse *period_str* in any supported format and return 'YYYY-MM'.

    Accepted formats:
        "2024-11"        → "2024-11"
        "2024-11-01"     → "2024-11"
        "November 2024"  → "2024-11"
        "Nov 2024"       → "2024-11"
        "11/2024"        → "2024-11"
        "Q3 2024"        → "2024-07"  (first month of the quarter)

    Raises ValueError if the string cannot be parsed.
    """
    s = period_str.strip()

    # Already YYYY-MM
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s

    # YYYY-MM-DD  →  YYYY-MM
    m = re.fullmatch(r"(\d{4})-(\d{2})-\d{2}", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # Q1/Q2/Q3/Q4 YYYY
    m = re.fullmatch(r"(Q[1-4])\s+(\d{4})", s, re.IGNORECASE)
    if m:
        quarter = m.group(1).upper()
        year = m.group(2)
        return f"{year}-{_QUARTER_TO_MONTH[quarter]}"

    # MM/YYYY
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"

    # "November 2024" or "Nov 2024"
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", s)
    if m:
        month_num = _MONTH_NAME_TO_NUM.get(m.group(1).lower())
        if month_num:
            return f"{m.group(2)}-{month_num}"

    raise ValueError(f"Cannot parse period string: '{period_str}'")


# ---------------------------------------------------------------------------
# validate_record
# ---------------------------------------------------------------------------

def validate_record(record: dict) -> tuple[bool, str]:
    """Validate a raw price record against the pipeline schema.

    Checks:
      - All required fields are present and non-empty:
        source, region, fuel_type, price, unit, period
      - price is numeric, > 0, and < 10.0 (sanity cap — electricity > $10/kWh
        indicates a data error; raw unit values should be < 10 before conversion)
      - region is a known two-letter US state code

    Returns (True, "") on success or (False, reason_string) on failure.
    """
    required_fields = ("source", "region", "fuel_type", "price", "unit", "period")
    for field in required_fields:
        val = record.get(field)
        if val is None or val == "":
            return False, f"Missing required field: '{field}'"

    try:
        price = float(record["price"])
    except (TypeError, ValueError):
        return False, f"Price is not numeric: {record['price']!r}"

    if price <= 0:
        return False, f"Price must be > 0, got {price}"

    region = str(record["region"]).upper()
    if region not in STATE_REGION_MAP:
        return False, f"Unknown region code: '{region}'"

    return True, ""


# ---------------------------------------------------------------------------
# deduplicate_records
# ---------------------------------------------------------------------------

def deduplicate_records(records: list[dict], db: Session) -> list[dict]:
    """Filter *records* to those not already stored in price_snapshots.

    Queries the DB for each (source, region, fuel_type, period) combination
    present in *records* and returns only the new ones.

    Logs: "Deduplication: {total} incoming, {dupes} duplicates skipped, {new} new records"
    """
    if not records:
        return []

    existing: set[tuple] = set()
    for rec in records:
        key = (rec.get("source"), rec.get("region"), rec.get("fuel_type"), rec.get("period"))
        if key in existing:
            continue  # already flagged as duplicate within this batch
        match = (
            db.query(PriceSnapshot)
            .filter_by(
                source=key[0],
                region=key[1],
                fuel_type=key[2],
                period=key[3],
            )
            .first()
        )
        if match:
            existing.add(key)

    new_records = [
        r for r in records
        if (r.get("source"), r.get("region"), r.get("fuel_type"), r.get("period"))
        not in existing
    ]

    dupes = len(records) - len(new_records)
    logger.info(
        "Deduplication: %d incoming, %d duplicates skipped, %d new records",
        len(records), dupes, len(new_records),
    )
    return new_records


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _redis_client() -> redis_lib.Redis:
    return redis_lib.from_url(settings.redis_url, decode_responses=True)


def _store_pipeline_stats(stats: dict, sources: list[str]) -> None:
    """Persist pipeline run stats to Redis for the pipeline-stats endpoint."""
    try:
        r = _redis_client()
        entry = {
            **stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "sources": list(set(sources)),
        }
        # Append to a capped list (keep last 10 000 entries)
        pipe = r.pipeline()
        pipe.rpush("pipeline_stats", json.dumps(entry))
        pipe.ltrim("pipeline_stats", -10_000, -1)
        pipe.execute()
    except Exception as exc:
        logger.error("Failed to persist pipeline stats to Redis: %s", exc)


# ---------------------------------------------------------------------------
# normalize_pipeline  (main entry point)
# ---------------------------------------------------------------------------

def normalize_pipeline(raw_records: list[dict], db: Session) -> dict:
    """Run the full normalization pipeline on *raw_records*.

    Per-record steps (in order):
      1. validate_record       — reject structurally invalid records
      2. normalize_unit        — convert price to $/kWh or $/MMBtu
      3. normalize_period      — standardize period to YYYY-MM

    After processing all records:
      4. deduplicate_records   — drop records already in DB
      5. bulk_save_objects     — insert new records into price_snapshots

    Rejected records are pushed to the Redis list ``rejected_records`` for
    offline inspection.

    Returns a summary dict:
        total_received  — all records fed in
        validated       — records that passed all checks AND are new (= inserted)
        rejected        — records that failed validation or normalization
        duplicates      — valid records already present in DB
        inserted        — rows written to price_snapshots
        rejected_reasons — list of human-readable rejection messages
    """
    total_received = len(raw_records)
    valid_normalized: list[dict] = []
    rejected_entries: list[dict] = []
    rejected_reasons: list[str] = []
    sources: list[str] = [r.get("source", "") for r in raw_records if r.get("source")]

    for rec in raw_records:
        # Step 1 — structural validation
        ok, reason = validate_record(rec)
        if not ok:
            logger.warning("Record rejected [%s]: %s", rec.get("source", "?"), reason)
            rejected_entries.append({"record": rec, "reason": reason})
            rejected_reasons.append(reason)
            continue

        # Step 2 — unit normalization
        try:
            norm_price, norm_unit = normalize_unit(float(rec["price"]), rec["unit"])
        except ValueError as exc:
            reason = str(exc)
            logger.warning("Unit normalization failed: %s", reason)
            rejected_entries.append({"record": rec, "reason": reason})
            rejected_reasons.append(reason)
            continue

        # Sanity cap: electricity > $10/kWh indicates a data error
        if norm_unit == "$/kWh" and norm_price >= 10.0:
            reason = f"Price sanity check failed: {norm_price} $/kWh >= $10/kWh"
            logger.warning("Record rejected [%s]: %s", rec.get("source", "?"), reason)
            rejected_entries.append({"record": rec, "reason": reason})
            rejected_reasons.append(reason)
            continue

        # Step 3 — period normalization
        try:
            norm_period = normalize_period(str(rec["period"]))
        except ValueError as exc:
            reason = str(exc)
            logger.warning("Period normalization failed: %s", reason)
            rejected_entries.append({"record": rec, "reason": reason})
            rejected_reasons.append(reason)
            continue

        valid_normalized.append({
            **rec,
            "price": norm_price,
            "unit": norm_unit,
            "period": norm_period,
        })

    # Persist rejected records to Redis for inspection
    if rejected_entries:
        try:
            r = _redis_client()
            pipe = r.pipeline()
            for entry in rejected_entries:
                pipe.rpush("rejected_records", json.dumps(entry, default=str))
            pipe.execute()
        except Exception as exc:
            logger.error("Failed to push rejected records to Redis: %s", exc)

    # Step 4 — deduplicate against DB
    new_records = deduplicate_records(valid_normalized, db)
    duplicates = len(valid_normalized) - len(new_records)

    # Step 5 — bulk insert
    inserted = 0
    if new_records:
        snapshots = [
            PriceSnapshot(
                source=r["source"],
                region=r["region"],
                fuel_type=r["fuel_type"],
                price=r["price"],
                unit=r["unit"],
                period=r["period"],
                raw_data=r.get("raw_data"),
            )
            for r in new_records
        ]
        db.bulk_save_objects(snapshots)
        db.commit()
        inserted = len(snapshots)
        logger.info("normalize_pipeline: inserted %d new records", inserted)

    summary = {
        "total_received": total_received,
        "validated": inserted,          # new valid records actually written
        "rejected": len(rejected_entries),
        "duplicates": duplicates,
        "inserted": inserted,
        "rejected_reasons": rejected_reasons,
    }

    _store_pipeline_stats(summary, sources)
    return summary
