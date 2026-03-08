"""Anomaly detection engine: computes rolling statistics and flags price deviations."""

import logging
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.alert_config import AlertConfig
from app.models.base import FuelType, Severity
from app.models.price_snapshot import PriceSnapshot
from app.services.normalization import STATE_REGION_MAP

logger = logging.getLogger(__name__)

# Units we consider "normalized" — only these feed into rolling stats
_NORMALIZED_UNITS = ("$/kWh", "$/MMBtu")

# Default threshold used when no AlertConfig exists for a combo
_DEFAULT_THRESHOLD_PCT = 15.0

# Human-readable unit labels for alert messages
_UNIT_LABELS: dict[str, str] = {
    "$/kWh":   "$/kWh",
    "$/MMBtu": "$/MMBtu",
}

# Fuel-specific contextual advice for alert messages
_FUEL_ADVICE: dict[str, dict[str, str]] = {
    "electricity": {
        "above": "Consider reviewing contract renewals or accelerating efficiency projects.",
        "below": "A potential opportunity to lock in favorable long-term rates.",
    },
    "natural_gas": {
        "above": "Review heating contracts and consider fuel-switching options.",
        "below": "Good time to hedge or pre-purchase supply at current rates.",
    },
}


# ---------------------------------------------------------------------------
# compute_rolling_stats
# ---------------------------------------------------------------------------

def compute_rolling_stats(
    db: Session,
    region: str,
    fuel_type: str,
    months: int = 6,
) -> dict | None:
    """Return rolling statistics for the last *months* months of price data.

    Only records with normalized units ($/kWh or $/MMBtu) are included.
    Prices are averaged per period when multiple sources report the same month.

    Returns None if fewer than 3 data points are available.
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=months * 31)
    cutoff_period = cutoff_dt.strftime("%Y-%m")

    rows = (
        db.query(
            PriceSnapshot.period,
            func.avg(PriceSnapshot.price).label("avg_price"),
        )
        .filter(
            PriceSnapshot.region == region,
            PriceSnapshot.fuel_type == fuel_type,
            PriceSnapshot.unit.in_(_NORMALIZED_UNITS),
            PriceSnapshot.price.isnot(None),
            PriceSnapshot.period >= cutoff_period,
        )
        .group_by(PriceSnapshot.period)
        .order_by(PriceSnapshot.period)
        .all()
    )

    if len(rows) < 3:
        logger.debug(
            "compute_rolling_stats: insufficient data for %s/%s (%d periods < 3)",
            region, fuel_type, len(rows),
        )
        return None

    period_avgs = [float(row.avg_price) for row in rows]

    rolling_avg = statistics.mean(period_avgs)
    rolling_std = statistics.stdev(period_avgs) if len(period_avgs) > 1 else 0.0

    return {
        "region": region,
        "fuel_type": fuel_type,
        "rolling_avg": rolling_avg,
        "rolling_std": rolling_std,
        "rolling_min": min(period_avgs),
        "rolling_max": max(period_avgs),
        "sample_count": len(period_avgs),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# get_latest_price
# ---------------------------------------------------------------------------

def get_latest_price(db: Session, region: str, fuel_type: str) -> dict | None:
    """Return the most recent normalized price snapshot for *region*/*fuel_type*."""
    row = (
        db.query(PriceSnapshot)
        .filter(
            PriceSnapshot.region == region,
            PriceSnapshot.fuel_type == fuel_type,
            PriceSnapshot.unit.in_(_NORMALIZED_UNITS),
            PriceSnapshot.price.isnot(None),
        )
        .order_by(PriceSnapshot.period.desc(), PriceSnapshot.created_at.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "price": float(row.price),
        "unit": row.unit,
        "period": row.period,
        "source": row.source,
    }


# ---------------------------------------------------------------------------
# classify_anomaly
# ---------------------------------------------------------------------------

def classify_anomaly(
    current_price: float,
    rolling_avg: float,
    rolling_std: float,
    threshold_pct: float,
) -> dict:
    """Classify whether *current_price* is an anomaly relative to the rolling baseline.

    Returns a full dict when an anomaly is detected:
        {deviation_pct, z_score, direction, severity}

    Returns {severity: None} when the deviation is below *threshold_pct*.
    """
    if rolling_avg == 0:
        return {"severity": None}

    deviation_pct = ((current_price - rolling_avg) / rolling_avg) * 100
    z_score = (current_price - rolling_avg) / rolling_std if rolling_std > 0 else 0.0
    direction = "above" if current_price >= rolling_avg else "below"

    abs_dev = abs(deviation_pct)
    if abs_dev < threshold_pct:
        return {"severity": None}

    severity = "critical" if abs_dev >= threshold_pct * 2 else "warning"

    return {
        "deviation_pct": round(deviation_pct, 2),
        "z_score": round(z_score, 4),
        "direction": direction,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# generate_alert_message
# ---------------------------------------------------------------------------

def generate_alert_message(
    region: str,
    fuel_type: str,
    current_price: float,
    rolling_avg: float,
    deviation_pct: float,
    direction: str,
    severity: str,
) -> str:
    """Build a human-readable alert string.

    Uses 🚨 for critical and ⚠️ for warning severity levels.
    """
    region_name = STATE_REGION_MAP.get(region, region)
    fuel_label = fuel_type.replace("_", " ")
    emoji = "🚨 CRITICAL" if severity == "critical" else "⚠️ WARNING"

    advice = (
        _FUEL_ADVICE
        .get(fuel_type, {})
        .get(direction, "Monitor closely for continued changes.")
    )

    return (
        f"{emoji}: {region_name} {fuel_label} prices are "
        f"{abs(deviation_pct):.1f}% {direction} the 6-month average. "
        f"Current: ${current_price:.4f} vs avg ${rolling_avg:.4f}. "
        f"{advice}"
    )


# ---------------------------------------------------------------------------
# run_anomaly_detection
# ---------------------------------------------------------------------------

def run_anomaly_detection(db: Session) -> dict:
    """Scan all active alert configs and all known region/fuel_type pairs for anomalies.

    For each alert_config (or unconfigured region+fuel_type pair):
      1. Fetch the latest normalized price.
      2. Compute the rolling baseline statistics.
      3. Classify the deviation.
      4. If anomalous: dedup-check and create an Alert record (config-backed only).

    Alert records are only persisted when a matching AlertConfig exists — the DB
    model requires a non-null alert_config_id FK.  Unconfigured combos are detected
    and logged but produce no persistent Alert rows.

    Returns a summary dict:
        configs_evaluated, anomalies_detected, alerts_created,
        alerts_skipped_dedup, regions_evaluated
    """
    configs_evaluated = 0
    anomalies_detected = 0
    alerts_created = 0
    alerts_skipped_dedup = 0
    evaluated_regions: set[str] = set()

    dedup_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    # ------------------------------------------------------------------
    # Part A: Process active AlertConfigs
    # ------------------------------------------------------------------
    active_configs = db.query(AlertConfig).filter_by(is_active=True).all()
    config_combos: set[tuple[str, str]] = set()

    for config in active_configs:
        configs_evaluated += 1
        region = config.region
        fuel_type = config.fuel_type.value if isinstance(config.fuel_type, FuelType) else str(config.fuel_type)
        combo = (region, fuel_type)
        config_combos.add(combo)
        evaluated_regions.add(region)

        latest = get_latest_price(db, region, fuel_type)
        if latest is None:
            logger.debug("No price data for config %s/%s — skipping", region, fuel_type)
            continue

        stats = compute_rolling_stats(db, region, fuel_type)
        if stats is None:
            logger.debug("Insufficient history for %s/%s — skipping", region, fuel_type)
            continue

        threshold = float(config.threshold_pct)
        result = classify_anomaly(
            current_price=latest["price"],
            rolling_avg=stats["rolling_avg"],
            rolling_std=stats["rolling_std"],
            threshold_pct=threshold,
        )

        if result["severity"] is None:
            continue

        anomalies_detected += 1

        # Dedup: skip if same config already fired in the last 24 hours
        recent = (
            db.query(Alert)
            .filter(
                Alert.alert_config_id == config.id,
                Alert.region == region,
                Alert.fuel_type == fuel_type,
                Alert.triggered_at >= dedup_cutoff,
            )
            .first()
        )
        if recent:
            alerts_skipped_dedup += 1
            logger.info(
                "Dedup: alert for %s/%s already fired within 24 h — skipping",
                region, fuel_type,
            )
            continue

        severity_enum = Severity(result["severity"])
        message = generate_alert_message(
            region=region,
            fuel_type=fuel_type,
            current_price=latest["price"],
            rolling_avg=stats["rolling_avg"],
            deviation_pct=result["deviation_pct"],
            direction=result["direction"],
            severity=result["severity"],
        )

        alert = Alert(
            alert_config_id=config.id,
            region=region,
            fuel_type=fuel_type,
            severity=severity_enum,
            current_price=latest["price"],
            rolling_avg_price=stats["rolling_avg"],
            deviation_pct=result["deviation_pct"],
            message=message,
            triggered_at=datetime.now(timezone.utc),
            notified=False,
        )
        db.add(alert)
        alerts_created += 1
        logger.info(
            "Alert created: %s %s/%s dev=%.1f%% price=%.4f",
            result["severity"].upper(), region, fuel_type,
            result["deviation_pct"], latest["price"],
        )

    db.commit()

    # ------------------------------------------------------------------
    # Part B: Detect-only pass on unconfigured region+fuel_type combos
    # (logs only — no Alert records can be created without a config FK)
    # ------------------------------------------------------------------
    all_combos = (
        db.query(PriceSnapshot.region, PriceSnapshot.fuel_type)
        .filter(PriceSnapshot.unit.in_(_NORMALIZED_UNITS))
        .distinct()
        .all()
    )

    for row in all_combos:
        region = row.region
        fuel_type_val = row.fuel_type.value if isinstance(row.fuel_type, FuelType) else str(row.fuel_type)
        combo = (region, fuel_type_val)
        evaluated_regions.add(region)

        if combo in config_combos:
            continue  # already handled in Part A

        latest = get_latest_price(db, region, fuel_type_val)
        if latest is None:
            continue

        stats = compute_rolling_stats(db, region, fuel_type_val)
        if stats is None:
            continue

        result = classify_anomaly(
            current_price=latest["price"],
            rolling_avg=stats["rolling_avg"],
            rolling_std=stats["rolling_std"],
            threshold_pct=_DEFAULT_THRESHOLD_PCT,
        )

        if result["severity"] is not None:
            anomalies_detected += 1
            logger.warning(
                "[no-config] Anomaly detected %s %s/%s dev=%.1f%% — "
                "create an AlertConfig to persist and notify",
                result["severity"].upper(), region, fuel_type_val,
                result["deviation_pct"],
            )

    return {
        "configs_evaluated": configs_evaluated,
        "anomalies_detected": anomalies_detected,
        "alerts_created": alerts_created,
        "alerts_skipped_dedup": alerts_skipped_dedup,
        "regions_evaluated": sorted(evaluated_regions),
    }
