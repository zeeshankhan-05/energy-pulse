"""Tests for the alert delivery service (alert_delivery.py)."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models.base import Base, FuelType, Severity
from app.models.alert import Alert
from app.models.alert_config import AlertConfig
from app.services.alert_delivery import (
    deliver_pending_alerts,
    send_email_alert,
    send_slack_alert,
    should_suppress_alert,
)


# ---------------------------------------------------------------------------
# SQLite fixture (same pattern as test_anomaly_detection.py)
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

def _make_config(db, region="IL", fuel_type=FuelType.electricity, threshold=15.0,
                 email="test@example.com", slack_webhook=None) -> AlertConfig:
    """Create and persist an AlertConfig."""
    config = AlertConfig(
        region=region,
        fuel_type=fuel_type,
        threshold_pct=threshold,
        email=email,
        slack_webhook=slack_webhook,
        is_active=True,
    )
    db.add(config)
    db.commit()
    return config


def _make_alert(db, config: AlertConfig, notified=False, hours_ago=0,
                severity=Severity.warning) -> Alert:
    """Create and persist an Alert tied to a config."""
    triggered = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    alert = Alert(
        alert_config_id=config.id,
        region=config.region,
        fuel_type=config.fuel_type,
        severity=severity,
        current_price=0.1900,
        rolling_avg_price=0.1200,
        deviation_pct=58.33,
        message="Test alert: price spike detected.",
        triggered_at=triggered,
        notified=notified,
        notified_at=triggered if notified else None,
    )
    db.add(alert)
    db.commit()
    return alert


# ---------------------------------------------------------------------------
# send_email_alert tests
# ---------------------------------------------------------------------------

@patch("app.services.alert_delivery.settings")
@patch("app.services.alert_delivery.SendGridAPIClient")
def test_send_email_alert_success(mock_sg_class, mock_settings, sqlite_session):
    """Mock SendGrid SDK: returns True on 202 response."""
    mock_settings.sendgrid_api_key = "SG.test-key"
    mock_settings.alert_from_email = "alerts@energypulse.app"
    mock_settings.alert_default_email = ""

    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_sg_instance = MagicMock()
    mock_sg_instance.send.return_value = mock_response
    mock_sg_class.return_value = mock_sg_instance

    config = _make_config(sqlite_session, email="user@example.com")
    alert = _make_alert(sqlite_session, config)

    result = send_email_alert(alert, config)

    assert result is True
    mock_sg_instance.send.assert_called_once()


@patch("app.services.alert_delivery.settings")
@patch("app.services.alert_delivery.SendGridAPIClient")
def test_send_email_alert_failure_4xx(mock_sg_class, mock_settings, sqlite_session):
    """SendGrid returns 4xx (e.g. bad API key) → returns False, does not raise."""
    mock_settings.sendgrid_api_key = "SG.bad-key"
    mock_settings.alert_from_email = "alerts@energypulse.app"
    mock_settings.alert_default_email = ""

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.body = b"Forbidden"
    mock_sg_instance = MagicMock()
    mock_sg_instance.send.return_value = mock_response
    mock_sg_class.return_value = mock_sg_instance

    config = _make_config(sqlite_session, email="user@example.com")
    alert = _make_alert(sqlite_session, config)

    result = send_email_alert(alert, config)

    assert result is False


# ---------------------------------------------------------------------------
# send_slack_alert tests
# ---------------------------------------------------------------------------

@patch("app.services.alert_delivery.httpx")
def test_send_slack_alert_success(mock_httpx, sqlite_session):
    """Mock httpx: returns True on 200 response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx.post.return_value = mock_response

    config = _make_config(sqlite_session)
    alert = _make_alert(sqlite_session, config)

    result = send_slack_alert(alert, "https://hooks.slack.com/test")

    assert result is True
    mock_httpx.post.assert_called_once()
    # Verify the payload contains Block Kit blocks
    call_kwargs = mock_httpx.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert "attachments" in payload
    assert len(payload["attachments"][0]["blocks"]) >= 3


@patch("app.services.alert_delivery.httpx")
def test_send_slack_alert_failure(mock_httpx, sqlite_session):
    """Slack returns non-200 → returns False, does not raise."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "internal error"
    mock_httpx.post.return_value = mock_response

    config = _make_config(sqlite_session)
    alert = _make_alert(sqlite_session, config)

    result = send_slack_alert(alert, "https://hooks.slack.com/test")

    assert result is False


# ---------------------------------------------------------------------------
# should_suppress_alert tests
# ---------------------------------------------------------------------------

def test_should_suppress_alert_recent(sqlite_session):
    """Returns True when same alert was already notified 2 hours ago."""
    config = _make_config(sqlite_session)
    # Create a *notified* alert from 2 hours ago
    _make_alert(sqlite_session, config, notified=True, hours_ago=2)

    result = should_suppress_alert(
        sqlite_session,
        region="IL",
        fuel_type=FuelType.electricity,
        severity=Severity.warning,
        suppression_hours=24,
    )
    assert result is True


def test_should_suppress_alert_expired(sqlite_session):
    """Returns False when last alert was 25 hours ago (outside 24h window)."""
    config = _make_config(sqlite_session)
    _make_alert(sqlite_session, config, notified=True, hours_ago=25)

    result = should_suppress_alert(
        sqlite_session,
        region="IL",
        fuel_type=FuelType.electricity,
        severity=Severity.warning,
        suppression_hours=24,
    )
    assert result is False


def test_should_suppress_alert_ignores_unnotified(sqlite_session):
    """An unnotified alert (notified=False) must NOT suppress its own delivery.

    This is the critical deadlock guard: if should_suppress_alert queried
    all alerts regardless of notified status, undelivered alerts would
    suppress their own delivery attempt and silently never send.
    """
    config = _make_config(sqlite_session)
    # Create a recent but *unnotified* alert
    _make_alert(sqlite_session, config, notified=False, hours_ago=1)

    result = should_suppress_alert(
        sqlite_session,
        region="IL",
        fuel_type=FuelType.electricity,
        severity=Severity.warning,
        suppression_hours=24,
    )
    # Must return False — the unnotified alert should NOT suppress delivery
    assert result is False


# ---------------------------------------------------------------------------
# deliver_pending_alerts tests
# ---------------------------------------------------------------------------

@patch("app.services.alert_delivery.send_slack_alert", return_value=False)
@patch("app.services.alert_delivery.send_email_alert", return_value=True)
@patch("app.services.alert_delivery.settings")
def test_deliver_pending_alerts_processes_unnotified(
    mock_settings, mock_email, mock_slack, sqlite_session,
):
    """Only notified=False alerts are processed; notified=True alerts are skipped."""
    mock_settings.alert_default_email = "default@example.com"
    mock_settings.alert_slack_webhook = ""

    config = _make_config(sqlite_session, email="user@example.com")

    # One unnotified alert → should be processed
    unnotified = _make_alert(sqlite_session, config, notified=False)
    # One already-notified alert → should be skipped
    already_sent = _make_alert(sqlite_session, config, notified=True, hours_ago=1)

    summary = deliver_pending_alerts(sqlite_session)

    assert summary["alerts_processed"] == 1
    assert summary["email_sent"] == 1

    # Verify the unnotified alert was marked as delivered
    sqlite_session.refresh(unnotified)
    assert unnotified.notified is True
    assert unnotified.notified_at is not None

    # Verify the already-notified alert wasn't touched
    sqlite_session.refresh(already_sent)
    assert already_sent.notified is True


@patch("app.services.alert_delivery.send_slack_alert", return_value=False)
@patch("app.services.alert_delivery.send_email_alert", return_value=False)
@patch("app.services.alert_delivery.settings")
def test_deliver_pending_alerts_all_channels_fail(
    mock_settings, mock_email, mock_slack, sqlite_session,
):
    """When all delivery channels fail, alert stays notified=False for retry."""
    mock_settings.alert_default_email = "default@example.com"
    mock_settings.alert_slack_webhook = "https://hooks.slack.com/test"

    config = _make_config(sqlite_session, email="user@example.com")
    alert = _make_alert(sqlite_session, config, notified=False)

    summary = deliver_pending_alerts(sqlite_session)

    assert summary["alerts_processed"] == 1
    assert summary["delivery_failures"] == 1

    sqlite_session.refresh(alert)
    assert alert.notified is False
    assert alert.notified_at is None
