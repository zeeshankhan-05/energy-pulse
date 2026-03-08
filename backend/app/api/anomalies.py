"""API router: anomaly alerts, rolling statistics, and alert configuration CRUD."""

import logging
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.alert import Alert
from app.models.alert_config import AlertConfig
from app.models.base import FuelType
from app.services.anomaly_detection import compute_rolling_stats
from app.services.normalization import STATE_REGION_MAP

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["anomalies"])

# ---------------------------------------------------------------------------
# Known values for validation
# ---------------------------------------------------------------------------
_VALID_REGIONS = set(STATE_REGION_MAP.keys())
_VALID_FUEL_TYPES = {ft.value for ft in FuelType}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AlertConfigCreate(BaseModel):
    """Request body for creating an alert configuration."""
    region: str = Field(..., min_length=2, max_length=10, description="US state code, e.g. IL")
    fuel_type: str = Field(..., description="'electricity' or 'natural_gas'")
    threshold_pct: float = Field(..., ge=5.0, le=50.0, description="Deviation % that triggers an alert")
    email: str | None = Field(None, max_length=255, description="Optional email address for notifications")
    slack_webhook: str | None = Field(None, max_length=512, description="Optional Slack webhook URL")


# ---------------------------------------------------------------------------
# Anomaly list / stats (existing endpoints, prefixed under /api/anomalies)
# ---------------------------------------------------------------------------

@router.get("/anomalies")
def list_anomalies(db: Session = Depends(get_db)) -> list[dict]:
    """Return the last 50 alerts ordered by triggered_at descending."""
    alerts = (
        db.query(Alert)
        .order_by(Alert.triggered_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": str(a.id),
            "region": a.region,
            "fuel_type": a.fuel_type.value if isinstance(a.fuel_type, FuelType) else str(a.fuel_type),
            "severity": a.severity.value if hasattr(a.severity, "value") else str(a.severity),
            "deviation_pct": float(a.deviation_pct),
            "current_price": float(a.current_price),
            "rolling_avg_price": float(a.rolling_avg_price),
            "message": a.message,
            "triggered_at": a.triggered_at.isoformat(),
            "notified": a.notified,
            "notified_at": a.notified_at.isoformat() if a.notified_at else None,
        }
        for a in alerts
    ]


@router.get("/anomalies/stats")
def anomaly_stats(db: Session = Depends(get_db)) -> dict:
    """Aggregate alert counts grouped by region and severity for the last 30 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    rows = (
        db.query(
            Alert.region,
            Alert.fuel_type,
            Alert.severity,
            func.count(Alert.id).label("count"),
        )
        .filter(Alert.triggered_at >= cutoff)
        .group_by(Alert.region, Alert.fuel_type, Alert.severity)
        .order_by(Alert.region, Alert.fuel_type, Alert.severity)
        .all()
    )

    # Nest as: region → fuel_type → severity → count
    result: dict = {}
    for row in rows:
        region = row.region
        fuel_type = row.fuel_type.value if isinstance(row.fuel_type, FuelType) else str(row.fuel_type)
        severity = row.severity.value if hasattr(row.severity, "value") else str(row.severity)
        result.setdefault(region, {}).setdefault(fuel_type, {})[severity] = row.count

    return {
        "period_days": 30,
        "from_date": cutoff.strftime("%Y-%m-%d"),
        "by_region": result,
    }


@router.get("/anomalies/rolling-stats")
def rolling_stats(
    region: str = Query(..., description="Two-letter US state code, e.g. IL"),
    fuel_type: str = Query(..., description="'electricity' or 'natural_gas'"),
    months: int = Query(6, ge=1, le=24, description="Look-back window in months"),
    db: Session = Depends(get_db),
) -> dict:
    """Return rolling price statistics for any region + fuel_type combination."""
    stats = compute_rolling_stats(db, region.upper(), fuel_type, months=months)
    if stats is None:
        raise HTTPException(
            status_code=404,
            detail=f"Insufficient data for {region}/{fuel_type} (need at least 3 months).",
        )
    return stats


# ---------------------------------------------------------------------------
# Alert config CRUD
# ---------------------------------------------------------------------------

@router.post("/alert-configs")
def create_alert_config(body: AlertConfigCreate, db: Session = Depends(get_db)) -> dict:
    """Create a new alert configuration for a region + fuel type pair."""
    region = body.region.upper()
    if region not in _VALID_REGIONS:
        raise HTTPException(status_code=422, detail=f"Unknown region: '{region}'. Must be a US state code.")
    if body.fuel_type not in _VALID_FUEL_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid fuel_type: '{body.fuel_type}'. Must be one of {_VALID_FUEL_TYPES}.")

    config = AlertConfig(
        region=region,
        fuel_type=body.fuel_type,
        threshold_pct=body.threshold_pct,
        email=body.email,
        slack_webhook=body.slack_webhook,
        is_active=True,
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    logger.info("Created alert config %s for %s/%s", config.id, region, body.fuel_type)
    return _config_to_dict(config)


@router.get("/alert-configs")
def list_alert_configs(db: Session = Depends(get_db)) -> list[dict]:
    """List all active alert configurations."""
    configs = db.query(AlertConfig).filter_by(is_active=True).all()
    return [_config_to_dict(c) for c in configs]


@router.delete("/alert-configs/{config_id}")
def delete_alert_config(config_id: str, db: Session = Depends(get_db)) -> dict:
    """Soft-delete an alert configuration by setting is_active=False."""
    try:
        uid = uuid_mod.UUID(config_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format.")

    config = db.query(AlertConfig).filter(AlertConfig.id == uid).first()
    if config is None:
        raise HTTPException(status_code=404, detail="Alert config not found.")

    config.is_active = False
    db.commit()
    logger.info("Soft-deleted alert config %s", config_id)
    return {"deleted": True, "id": config_id}


def _config_to_dict(c: AlertConfig) -> dict:
    """Serialize an AlertConfig to a response dict."""
    return {
        "id": str(c.id),
        "region": c.region,
        "fuel_type": c.fuel_type.value if isinstance(c.fuel_type, FuelType) else str(c.fuel_type),
        "threshold_pct": float(c.threshold_pct),
        "email": c.email,
        "slack_webhook": c.slack_webhook,
        "is_active": c.is_active,
    }


# ---------------------------------------------------------------------------
# Alert test endpoint
# ---------------------------------------------------------------------------

@router.post("/alerts/{alert_id}/test")
def test_alert_delivery(alert_id: str, db: Session = Depends(get_db)) -> dict:
    """Manually trigger delivery for a specific alert regardless of notified status.

    This re-triggers delivery on an **existing** Alert row — it does NOT create
    a new Alert. The alert_config_id FK is nullable=False, so bare alerts
    without a config cannot exist.

    Useful for testing email/Slack webhook configuration.
    """
    from app.services.alert_delivery import send_email_alert, send_slack_alert
    from app.config import settings

    try:
        uid = uuid_mod.UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format.")

    alert = db.query(Alert).filter(Alert.id == uid).first()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found.")

    # Look up the associated config for email/Slack targets
    config = (
        db.query(AlertConfig)
        .filter(AlertConfig.id == alert.alert_config_id)
        .first()
    )

    results = {"email_sent": False, "slack_sent": False}

    # Attempt email
    if config:
        results["email_sent"] = send_email_alert(alert, config)

    # Attempt Slack
    webhook_url = (config.slack_webhook if config and config.slack_webhook else None) or settings.alert_slack_webhook
    if webhook_url:
        results["slack_sent"] = send_slack_alert(alert, webhook_url)

    return {
        "alert_id": alert_id,
        **results,
    }
