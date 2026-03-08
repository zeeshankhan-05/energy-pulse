"""API router: manual job triggers and demo data seeding."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.base import FuelType
from app.models.price_snapshot import PriceSnapshot
from app.services.anomaly_detection import run_anomaly_detection
from app.services.alert_delivery import deliver_pending_alerts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/trigger/anomalies")
def trigger_anomaly_detection(db: Session = Depends(get_db)) -> dict:
    """Run anomaly detection synchronously and return the summary."""
    logger.info("Manual trigger: run_anomaly_detection")
    result = run_anomaly_detection(db)
    return result


@router.post("/trigger/deliver")
def trigger_alert_delivery(db: Session = Depends(get_db)) -> dict:
    """Run alert delivery synchronously and return the summary."""
    logger.info("Manual trigger: deliver_pending_alerts")
    result = deliver_pending_alerts(db)
    return result


@router.post("/seed-demo")
def seed_demo_data(db: Session = Depends(get_db)) -> dict:
    """Seed 6 months of IL electricity prices with a spike in the current month.

    Useful for manual end-to-end testing without a live EIA API key.
    Idempotent — skips records that already exist (matched on source+region+fuel_type+period).
    """
    from app.models.alert_config import AlertConfig
    from sqlalchemy.exc import IntegrityError

    region = "IL"
    fuel_type = FuelType.electricity
    source = "DEMO"

    # Baseline: 5 months of ~$0.12/kWh prices
    base_price = 0.1200
    now = datetime.now(timezone.utc)
    inserted = 0

    for i in range(5, 0, -1):
        dt = now - timedelta(days=i * 31)
        period = dt.strftime("%Y-%m")
        exists = (
            db.query(PriceSnapshot)
            .filter_by(source=source, region=region, fuel_type=fuel_type, period=period)
            .first()
        )
        if exists:
            continue
        db.add(PriceSnapshot(
            source=source,
            region=region,
            fuel_type=fuel_type,
            price=base_price,
            unit="$/kWh",
            period=period,
        ))
        inserted += 1

    # Spike: current month at $0.19/kWh (~58% above baseline)
    spike_period = now.strftime("%Y-%m")
    exists = (
        db.query(PriceSnapshot)
        .filter_by(source=source, region=region, fuel_type=fuel_type, period=spike_period)
        .first()
    )
    if not exists:
        db.add(PriceSnapshot(
            source=source,
            region=region,
            fuel_type=fuel_type,
            price=0.1900,
            unit="$/kWh",
            period=spike_period,
        ))
        inserted += 1

    # Ensure there's an AlertConfig for IL electricity (idempotent)
    config = (
        db.query(AlertConfig)
        .filter_by(region=region, fuel_type=fuel_type, is_active=True)
        .first()
    )
    config_created = False
    if config is None:
        config = AlertConfig(
            region=region,
            fuel_type=fuel_type,
            threshold_pct=15.0,
            is_active=True,
        )
        db.add(config)
        config_created = True

    db.commit()

    return {
        "records_inserted": inserted,
        "alert_config_created": config_created,
        "region": region,
        "fuel_type": "electricity",
        "spike_period": spike_period,
        "baseline_price": base_price,
        "spike_price": 0.1900,
    }
