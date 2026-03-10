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
    duplicate_counts: dict[tuple, int] = defaultdict(int)
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
                duplicate_counts[key] += entry.get("duplicates", 0)
                reason_buckets[key].extend(entry.get("rejected_reasons", []))
    except Exception as exc:
        logger.warning("Could not read pipeline_stats from Redis: %s", exc)

    # ------------------------------------------------------------------
    # Merge into a unified list of daily-source stats
    # ------------------------------------------------------------------
    all_keys: set[tuple] = set(insert_counts) | set(reject_counts) | set(duplicate_counts)
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
            "records_duplicates": duplicate_counts.get(key, 0),
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
    cache_key = f"cache:prices:{region}:{fuel_type}:{months}"
    try:
        r = _redis_client()
        cached = r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.warning("Redis cache error: %s", e)
        r = None

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

    result = [
        {
            "period": row.period,
            "price": float(row.price) if row.price is not None else None,
            "unit": row.unit,
            "source": row.source,
            "region": row.region,
            "fuel_type": row.fuel_type.value if hasattr(row.fuel_type, "value") else str(row.fuel_type),
        }
        for row in rows
    ]
    
    try:
        if r:
            r.setex(cache_key, 1800, json.dumps(result))
    except Exception as e:
        logger.warning("Redis cache set error: %s", e)

    return result



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
    cache_key = f"cache:prices:latest:{region}:{limit}"
    try:
        r = _redis_client()
        cached = r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.warning("Redis cache error: %s", e)
        r = None

    rows = (
        db.query(PriceSnapshot)
        .filter(PriceSnapshot.region == region.upper())
        .order_by(PriceSnapshot.created_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for row in rows:
        source_url = SOURCE_URLS.get(
            row.source, f"https://www.google.com/search?q={row.source}"
        )
        result.append(
            {
                "timestamp": row.created_at.isoformat() if row.created_at else None,
                "region": row.region,
                "fuel_type": row.fuel_type.value if hasattr(row.fuel_type, "value") else str(row.fuel_type),
                "price": float(row.price) if row.price is not None else None,
                "unit": row.unit,
                "source": row.source,
                "source_url": source_url,
            }
        )

    try:
        if r:
            r.setex(cache_key, 1800, json.dumps(result))
    except Exception as e:
        logger.warning("Redis cache set error: %s", e)

    return result


@router.get("/regions")
def get_regions(db: Session = Depends(get_db)) -> list[str]:
    """Return a sorted list of all distinct regions in the price_snapshots table."""
    cache_key = "cache:regions"
    try:
        r = _redis_client()
        cached = r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.warning("Redis cache error: %s", e)
        r = None

    rows = (
        db.query(PriceSnapshot.region)
        .distinct()
        .order_by(PriceSnapshot.region.asc())
        .all()
    )
    result = [row.region for row in rows]
    if "AZ" not in result:
        result.append("AZ")
        result.sort()
    
    try:
        if r:
            r.setex(cache_key, 3600, json.dumps(result))
    except Exception as e:
        logger.warning("Redis cache set error: %s", e)
        
    return result

