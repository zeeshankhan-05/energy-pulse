"""API router: data ingestion and pipeline statistics."""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import redis as redis_lib
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.price_snapshot import PriceSnapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/data", tags=["data"])


def _redis_client() -> redis_lib.Redis:
    return redis_lib.from_url(settings.redis_url, decode_responses=True)


@router.get("/pipeline-stats")
def get_pipeline_stats(db: Session = Depends(get_db)) -> dict:
    """Return ingestion statistics for the last 7 days, grouped by date and source.

    Each entry includes records_inserted, records_rejected, and top rejection reasons
    derived from the ``pipeline_stats`` Redis list and the ``price_snapshots`` table.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # DB: count inserts per date + source over the last 7 days
    # ------------------------------------------------------------------
    db_rows = (
        db.query(
            func.date(PriceSnapshot.created_at).label("date"),
            PriceSnapshot.source,
            func.count(PriceSnapshot.id).label("count"),
        )
        .filter(PriceSnapshot.created_at >= cutoff)
        .group_by(func.date(PriceSnapshot.created_at), PriceSnapshot.source)
        .all()
    )

    # Keyed by (date_str, source)
    insert_counts: dict[tuple, int] = {
        (str(row.date), row.source): row.count for row in db_rows
    }

    # ------------------------------------------------------------------
    # Redis: read pipeline_stats entries for the last 7 days
    # ------------------------------------------------------------------
    reject_counts: dict[tuple, int] = defaultdict(int)
    reason_buckets: dict[tuple, list[str]] = defaultdict(list)

    try:
        r = _redis_client()
        raw_entries = r.lrange("pipeline_stats", 0, -1)
        for raw in raw_entries:
            try:
                entry = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            entry_date = entry.get("date", "")
            if entry_date < cutoff_str:
                continue
            for src in entry.get("sources") or ["unknown"]:
                key = (entry_date, src)
                reject_counts[key] += entry.get("rejected", 0)
                reason_buckets[key].extend(entry.get("rejected_reasons", []))
    except Exception as exc:
        logger.warning("Could not read pipeline_stats from Redis: %s", exc)

    # ------------------------------------------------------------------
    # Merge into a unified list of daily-source stats
    # ------------------------------------------------------------------
    all_keys: set[tuple] = set(insert_counts) | set(reject_counts)
    stats_by_date: dict[str, list[dict]] = defaultdict(list)

    for date_str, source in sorted(all_keys):
        key = (date_str, source)
        reasons = reason_buckets.get(key, [])
        # Top 5 most frequent rejection reasons
        reason_freq: dict[str, int] = defaultdict(int)
        for r in reasons:
            reason_freq[r] += 1
        top_reasons = sorted(reason_freq, key=reason_freq.__getitem__, reverse=True)[:5]

        stats_by_date[date_str].append({
            "source": source,
            "records_inserted": insert_counts.get(key, 0),
            "records_rejected": reject_counts.get(key, 0),
            "top_rejection_reasons": top_reasons,
        })

    return {
        "period_days": 7,
        "from_date": cutoff_str,
        "daily_stats": [
            {"date": date_str, "sources": sources}
            for date_str, sources in sorted(stats_by_date.items())
        ],
    }


@router.get("/prices")
def get_prices(
    region: str = Query(..., description="Two-letter US state code, e.g. IL"),
    fuel_type: str | None = Query(None, description="'electricity' or 'natural_gas' — omit to return all fuel types"),
    months: int = Query(6, ge=1, le=24, description="Look-back window in months"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return price history for a region over the last N months.

    Only returns rows with normalized units ($/kWh or $/MMBtu) to avoid
    duplicate entries from raw-unit data. If fuel_type is omitted, all fuel
    types are returned.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 31)
    cutoff_period = cutoff.strftime("%Y-%m")

    q = (
        db.query(PriceSnapshot)
        .filter(
            PriceSnapshot.region == region.upper(),
            PriceSnapshot.unit.in_(["$/kWh", "$/MMBtu"]),
            PriceSnapshot.period >= cutoff_period,
        )
    )
    if fuel_type is not None:
        q = q.filter(PriceSnapshot.fuel_type == fuel_type)
    rows = q.order_by(PriceSnapshot.period.asc()).all()

    return [
        {
            "period": r.period,
            "price": float(r.price) if r.price is not None else None,
            "unit": r.unit,
            "source": r.source,
            "region": r.region,
            "fuel_type": r.fuel_type.value if hasattr(r.fuel_type, "value") else str(r.fuel_type),
        }
        for r in rows
    ]



SOURCE_URLS = {
    "EIA": "https://www.eia.gov/",
    "IL_PUC": "https://www.icc.illinois.gov/",
    "TX_PUC": "https://www.puc.texas.gov/",
    "OH_PUC": "https://puco.ohio.gov/",
}

@router.get("/prices/latest")
def get_latest_prices(
    region: str = Query(..., description="Two-letter US state code, e.g. IL"),
    limit: int = Query(100, ge=1, le=1000, description="Max number of rows to return"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return the most recently fetched price snapshots for a given region."""
    rows = (
        db.query(PriceSnapshot)
        .filter(PriceSnapshot.region == region.upper())
        .order_by(PriceSnapshot.created_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for r in rows:
        source_url = SOURCE_URLS.get(
            r.source, f"https://www.google.com/search?q={r.source}"
        )
        result.append(
            {
                "timestamp": r.created_at.isoformat() if r.created_at else None,
                "region": r.region,
                "fuel_type": r.fuel_type.value if hasattr(r.fuel_type, "value") else str(r.fuel_type),
                "price": float(r.price) if r.price is not None else None,
                "unit": r.unit,
                "source": r.source,
                "source_url": source_url,
            }
        )
    return result


@router.get("/regions")
def get_regions(db: Session = Depends(get_db)) -> list[str]:
    """Return a sorted list of all distinct regions in the price_snapshots table."""
    rows = (
        db.query(PriceSnapshot.region)
        .distinct()
        .order_by(PriceSnapshot.region.asc())
        .all()
    )
    return [r.region for r in rows]

