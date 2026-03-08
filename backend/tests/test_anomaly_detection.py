"""Tests for the anomaly detection engine (anomaly_detection.py)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models.base import Base, FuelType, Severity
from app.models.alert import Alert
from app.models.alert_config import AlertConfig
from app.models.price_snapshot import PriceSnapshot
from app.services.anomaly_detection import (
    classify_anomaly,
    compute_rolling_stats,
    generate_alert_message,
    get_latest_price,
    run_anomaly_detection,
)


# ---------------------------------------------------------------------------
# SQLite fixture (enables FK enforcement so cascade works)
# ---------------------------------------------------------------------------

@pytest.fixture()
def sqlite_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    # Enable FK enforcement in SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_period(months_ago: int) -> str:
    """Return a YYYY-MM period string N months in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=months_ago * 31)
    return dt.strftime("%Y-%m")


def _seed_il_electricity(
    db,
    normal_price: float = 0.12,
    spike_price: float | None = None,
    normal_months: int = 5,
):
    """Insert N months of normal IL electricity prices and optionally a spike for the current month."""
    for i in range(normal_months, 0, -1):
        period = _make_period(i)
        db.add(PriceSnapshot(
            source="DEMO",
            region="IL",
            fuel_type=FuelType.electricity,
            price=normal_price,
            unit="$/kWh",
            period=period,
        ))

    if spike_price is not None:
        db.add(PriceSnapshot(
            source="DEMO",
            region="IL",
            fuel_type=FuelType.electricity,
            price=spike_price,
            unit="$/kWh",
            period=_make_period(0),
        ))

    db.commit()


def _make_alert_config(db, region="IL", fuel_type=FuelType.electricity, threshold=15.0) -> AlertConfig:
    config = AlertConfig(
        region=region,
        fuel_type=fuel_type,
        threshold_pct=threshold,
        is_active=True,
    )
    db.add(config)
    db.commit()
    return config


# ---------------------------------------------------------------------------
# classify_anomaly tests
# ---------------------------------------------------------------------------

def test_classify_anomaly_warning_20pct_above():
    """Price 20% above average with default 15% threshold → 'warning'."""
    result = classify_anomaly(
        current_price=0.144,   # 20% above 0.12
        rolling_avg=0.12,
        rolling_std=0.005,
        threshold_pct=15.0,
    )
    assert result["severity"] == "warning"
    assert result["direction"] == "above"
    assert pytest.approx(result["deviation_pct"], abs=0.1) == 20.0


def test_classify_anomaly_critical_35pct_above():
    """Price 35% above average → 'critical' (>= 2× threshold)."""
    result = classify_anomaly(
        current_price=0.162,   # 35% above 0.12
        rolling_avg=0.12,
        rolling_std=0.005,
        threshold_pct=15.0,
    )
    assert result["severity"] == "critical"
    assert result["direction"] == "above"
    assert pytest.approx(result["deviation_pct"], abs=0.1) == 35.0


def test_classify_anomaly_no_anomaly_5pct_above():
    """Price only 5% above average → no anomaly (below threshold)."""
    result = classify_anomaly(
        current_price=0.126,   # 5% above 0.12
        rolling_avg=0.12,
        rolling_std=0.005,
        threshold_pct=15.0,
    )
    assert result["severity"] is None


def test_classify_anomaly_below_average():
    """Price 20% below average → 'warning' with direction 'below'."""
    result = classify_anomaly(
        current_price=0.096,   # 20% below 0.12
        rolling_avg=0.12,
        rolling_std=0.005,
        threshold_pct=15.0,
    )
    assert result["severity"] == "warning"
    assert result["direction"] == "below"
    assert result["deviation_pct"] < 0


def test_classify_anomaly_zero_std():
    """Zero standard deviation should not crash; z_score reported as 0."""
    result = classify_anomaly(
        current_price=0.15,
        rolling_avg=0.12,
        rolling_std=0.0,
        threshold_pct=15.0,
    )
    assert result["severity"] == "warning"
    assert result["z_score"] == 0.0


def test_classify_anomaly_exactly_at_threshold():
    """A deviation exactly equal to threshold is still a warning."""
    result = classify_anomaly(
        current_price=0.138,   # exactly 15% above 0.12
        rolling_avg=0.12,
        rolling_std=0.005,
        threshold_pct=15.0,
    )
    assert result["severity"] == "warning"


# ---------------------------------------------------------------------------
# generate_alert_message tests
# ---------------------------------------------------------------------------

def test_generate_alert_message_contains_region_and_price():
    msg = generate_alert_message(
        region="IL",
        fuel_type="electricity",
        current_price=0.144,
        rolling_avg=0.12,
        deviation_pct=20.0,
        direction="above",
        severity="warning",
    )
    assert "Illinois" in msg
    assert "0.144" in msg or "0.1440" in msg
    assert "⚠️" in msg
    assert "20.0%" in msg


def test_generate_alert_message_critical_uses_siren():
    msg = generate_alert_message(
        region="TX",
        fuel_type="natural_gas",
        current_price=12.5,
        rolling_avg=8.0,
        deviation_pct=56.25,
        direction="above",
        severity="critical",
    )
    assert "🚨" in msg
    assert "Texas" in msg or "TX" in msg


def test_generate_alert_message_below_includes_direction():
    msg = generate_alert_message(
        region="OH",
        fuel_type="electricity",
        current_price=0.096,
        rolling_avg=0.12,
        deviation_pct=-20.0,
        direction="below",
        severity="warning",
    )
    assert "below" in msg.lower()


# ---------------------------------------------------------------------------
# compute_rolling_stats tests
# ---------------------------------------------------------------------------

def test_compute_rolling_stats_returns_none_too_few_periods(sqlite_session):
    # Only 2 periods — below the 3-period minimum
    for i in (2, 1):
        sqlite_session.add(PriceSnapshot(
            source="X", region="IL", fuel_type=FuelType.electricity,
            price=0.12, unit="$/kWh", period=_make_period(i),
        ))
    sqlite_session.commit()

    result = compute_rolling_stats(sqlite_session, "IL", "electricity")
    assert result is None


def test_compute_rolling_stats_excludes_non_normalized_units(sqlite_session):
    # Insert 3 periods with raw "cents/kWh" (not normalized) + 0 with normalized
    for i in (3, 2, 1):
        sqlite_session.add(PriceSnapshot(
            source="X", region="IL", fuel_type=FuelType.electricity,
            price=12.0, unit="cents/kWh", period=_make_period(i),
        ))
    sqlite_session.commit()

    result = compute_rolling_stats(sqlite_session, "IL", "electricity")
    assert result is None  # no normalized-unit rows → None


def test_compute_rolling_stats_basic(sqlite_session):
    _seed_il_electricity(sqlite_session, normal_price=0.12, normal_months=5)

    result = compute_rolling_stats(sqlite_session, "IL", "electricity")
    assert result is not None
    assert pytest.approx(result["rolling_avg"], abs=1e-4) == 0.12
    assert result["sample_count"] >= 3
    assert result["rolling_min"] <= result["rolling_avg"] <= result["rolling_max"]
    assert "computed_at" in result


# ---------------------------------------------------------------------------
# get_latest_price tests
# ---------------------------------------------------------------------------

def test_get_latest_price_returns_most_recent(sqlite_session):
    _seed_il_electricity(sqlite_session, normal_price=0.12, normal_months=3)
    result = get_latest_price(sqlite_session, "IL", "electricity")
    assert result is not None
    assert result["price"] == pytest.approx(0.12, abs=1e-4)
    assert result["unit"] == "$/kWh"
    # _seed_il_electricity with normal_months=3 inserts months 3,2,1 ago — most recent is 1
    assert result["period"] == _make_period(1)


def test_get_latest_price_returns_none_when_empty(sqlite_session):
    result = get_latest_price(sqlite_session, "ZZ", "electricity")
    assert result is None


def test_get_latest_price_ignores_non_normalized(sqlite_session):
    sqlite_session.add(PriceSnapshot(
        source="X", region="IL", fuel_type=FuelType.electricity,
        price=13.5, unit="cents/kWh", period=_make_period(0),
    ))
    sqlite_session.commit()
    result = get_latest_price(sqlite_session, "IL", "electricity")
    assert result is None


# ---------------------------------------------------------------------------
# run_anomaly_detection — 1 alert created
# ---------------------------------------------------------------------------

def test_run_anomaly_detection_creates_alert(sqlite_session):
    """6 months of IL electricity data with a spike → exactly 1 alert created."""
    _make_alert_config(sqlite_session, region="IL",
                       fuel_type=FuelType.electricity, threshold=15.0)
    # 5 normal months at $0.12, spike this month at $0.19 (~58% above)
    _seed_il_electricity(
        sqlite_session,
        normal_price=0.1200,
        spike_price=0.1900,
        normal_months=5,
    )

    summary = run_anomaly_detection(sqlite_session)

    assert summary["configs_evaluated"] == 1
    assert summary["anomalies_detected"] >= 1
    assert summary["alerts_created"] == 1
    assert "IL" in summary["regions_evaluated"]

    alert = sqlite_session.query(Alert).first()
    assert alert is not None
    assert float(alert.current_price) == pytest.approx(0.19, abs=1e-4)
    assert alert.notified is False
    assert alert.message  # non-empty


# ---------------------------------------------------------------------------
# run_anomaly_detection — deduplication: 2 runs → only 1 alert
# ---------------------------------------------------------------------------

def test_run_anomaly_detection_dedup(sqlite_session):
    """Running detection twice on the same spike produces only 1 Alert row."""
    _make_alert_config(sqlite_session, region="IL",
                       fuel_type=FuelType.electricity, threshold=15.0)
    _seed_il_electricity(
        sqlite_session,
        normal_price=0.1200,
        spike_price=0.1900,
        normal_months=5,
    )

    first = run_anomaly_detection(sqlite_session)
    second = run_anomaly_detection(sqlite_session)

    assert first["alerts_created"] == 1
    assert second["alerts_created"] == 0
    assert second["alerts_skipped_dedup"] == 1

    total_alerts = sqlite_session.query(Alert).count()
    assert total_alerts == 1


# ---------------------------------------------------------------------------
# run_anomaly_detection — no anomaly when within threshold
# ---------------------------------------------------------------------------

def test_run_anomaly_detection_no_alert_within_threshold(sqlite_session):
    """A 5% deviation should NOT trigger an alert with 15% threshold."""
    _make_alert_config(sqlite_session, threshold=15.0)
    _seed_il_electricity(
        sqlite_session,
        normal_price=0.1200,
        spike_price=0.1260,   # exactly 5% above
        normal_months=5,
    )

    summary = run_anomaly_detection(sqlite_session)

    assert summary["alerts_created"] == 0
    assert sqlite_session.query(Alert).count() == 0


# ---------------------------------------------------------------------------
# run_anomaly_detection — insufficient history skips the config
# ---------------------------------------------------------------------------

def test_run_anomaly_detection_skips_insufficient_history(sqlite_session):
    """Only 2 months of data → no rolling stats → detection skipped."""
    _make_alert_config(sqlite_session, threshold=15.0)

    for i in (2, 1):
        sqlite_session.add(PriceSnapshot(
            source="X", region="IL", fuel_type=FuelType.electricity,
            price=0.12, unit="$/kWh", period=_make_period(i),
        ))
    sqlite_session.commit()

    summary = run_anomaly_detection(sqlite_session)

    assert summary["alerts_created"] == 0
    assert summary["anomalies_detected"] == 0
