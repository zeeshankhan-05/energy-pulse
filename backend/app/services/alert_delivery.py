"""Alert delivery service: email (SendGrid) and Slack (webhook) notifications.

Two layers of deduplication protect against alert spam:
  1. **Creation-time** (in anomaly_detection.py): prevents duplicate Alert *records*
     from being inserted for the same region+fuel_type within 24 hours.
  2. **Delivery-time** (should_suppress_alert here): prevents re-*sending*
     notifications for alerts that were already successfully delivered.
These serve different purposes and both are intentional.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, From, To, Subject, HtmlContent
from sqlalchemy.orm import Session

from app.config import settings
from app.models.alert import Alert
from app.models.alert_config import AlertConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email delivery via SendGrid
# ---------------------------------------------------------------------------

def send_email_alert(alert: Alert, config: AlertConfig) -> bool:
    """Send an HTML email notification for a triggered alert.

    Uses the SendGrid SDK. Returns True on 202 (accepted), False on any
    failure including 4xx responses (e.g. bad API key). Never raises.

    Args:
        alert: The Alert record to notify about.
        config: The associated AlertConfig containing the email address.

    Returns:
        True if the email was accepted by SendGrid, False otherwise.
    """
    recipient = config.email or settings.alert_default_email
    if not recipient:
        logger.debug("send_email_alert: no email configured for alert %s", alert.id)
        return False

    if not settings.sendgrid_api_key:
        logger.warning("send_email_alert: SENDGRID_API_KEY is not set — skipping")
        return False

    fuel_label = alert.fuel_type.value.replace("_", " ").title() if hasattr(alert.fuel_type, "value") else str(alert.fuel_type)
    severity_str = alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity)
    subject = f"[{severity_str.upper()}] Energy Price Alert — {alert.region} {fuel_label}"

    # Severity badge colors
    badge_bg = "#ef4444" if severity_str == "critical" else "#f59e0b"
    badge_label = "🚨 CRITICAL" if severity_str == "critical" else "⚠️ WARNING"

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#1a1a2e;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#1a1a2e;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#16213e;border-radius:12px;overflow:hidden;">
        <!-- Header with severity badge -->
        <tr>
          <td style="padding:24px 32px;background:#0f3460;">
            <span style="display:inline-block;padding:6px 16px;border-radius:6px;background:{badge_bg};color:#fff;font-weight:bold;font-size:14px;">
              {badge_label}
            </span>
            <h1 style="color:#e0e0e0;margin:16px 0 0;font-size:22px;">
              Energy Price Alert — {alert.region}
            </h1>
            <p style="color:#9ca3af;margin:4px 0 0;font-size:14px;">{fuel_label}</p>
          </td>
        </tr>
        <!-- Key stats -->
        <tr>
          <td style="padding:24px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:8px 0;">
                  <span style="color:#9ca3af;font-size:13px;">Current Price</span><br>
                  <span style="color:#e0e0e0;font-size:20px;font-weight:bold;">${float(alert.current_price):.4f}</span>
                </td>
                <td style="padding:8px 0;">
                  <span style="color:#9ca3af;font-size:13px;">Rolling Average</span><br>
                  <span style="color:#e0e0e0;font-size:20px;font-weight:bold;">${float(alert.rolling_avg_price):.4f}</span>
                </td>
                <td style="padding:8px 0;">
                  <span style="color:#9ca3af;font-size:13px;">Deviation</span><br>
                  <span style="color:{badge_bg};font-size:20px;font-weight:bold;">{float(alert.deviation_pct):+.1f}%</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <!-- Alert message -->
        <tr>
          <td style="padding:0 32px 24px;">
            <p style="color:#d1d5db;font-size:15px;line-height:1.6;margin:0;">
              {alert.message}
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:16px 32px;background:#0a0e27;border-top:1px solid #1e3a5f;">
            <p style="color:#6b7280;font-size:12px;margin:0;">
              Manage your alerts at <a href="https://energypulse.app" style="color:#3b82f6;">energypulse.app</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    try:
        message = Mail(
            from_email=From(settings.alert_from_email, "EnergyPulse Alerts"),
            to_emails=To(recipient),
            subject=Subject(subject),
            html_content=HtmlContent(html_body),
        )
        sg = SendGridAPIClient(settings.sendgrid_api_key)
        response = sg.send(message)

        if response.status_code == 202:
            logger.info("send_email_alert: sent to %s for alert %s", recipient, alert.id)
            return True
        else:
            # SendGrid 4xx (bad API key, invalid email, etc.)
            logger.warning(
                "send_email_alert: unexpected status %d for alert %s — body: %s",
                response.status_code, alert.id, response.body,
            )
            return False

    except Exception as exc:
        logger.error("send_email_alert: failed for alert %s — %s", alert.id, exc)
        return False


# ---------------------------------------------------------------------------
# Slack delivery via webhook
# ---------------------------------------------------------------------------

def send_slack_alert(alert: Alert, webhook_url: str) -> bool:
    """Post an alert notification to a Slack webhook using Block Kit.

    Args:
        alert: The Alert record to notify about.
        webhook_url: The Slack incoming webhook URL.

    Returns:
        True on HTTP 200, False on any failure. Never raises.
    """
    if not webhook_url:
        logger.debug("send_slack_alert: no webhook URL — skipping alert %s", alert.id)
        return False

    fuel_label = alert.fuel_type.value.replace("_", " ").title() if hasattr(alert.fuel_type, "value") else str(alert.fuel_type)
    severity_str = alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity)
    severity_emoji = "🚨" if severity_str == "critical" else "⚠️"
    color = "#ef4444" if severity_str == "critical" else "#f59e0b"

    triggered_at = alert.triggered_at.strftime("%Y-%m-%d %H:%M UTC") if alert.triggered_at else "N/A"

    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{severity_emoji} {severity_str.upper()}: {alert.region} {fuel_label} Price Alert",
                            "emoji": True,
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": alert.message,
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Region:*\n{alert.region}"},
                            {"type": "mrkdwn", "text": f"*Fuel Type:*\n{fuel_label}"},
                            {"type": "mrkdwn", "text": f"*Current Price:*\n${float(alert.current_price):.4f}"},
                            {"type": "mrkdwn", "text": f"*Rolling Avg:*\n${float(alert.rolling_avg_price):.4f}"},
                            {"type": "mrkdwn", "text": f"*Deviation:*\n{float(alert.deviation_pct):+.1f}%"},
                        ],
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"EnergyPulse • {triggered_at}",
                            }
                        ],
                    },
                ],
            }
        ]
    }

    try:
        response = httpx.post(webhook_url, json=payload, timeout=10.0)
        if response.status_code == 200:
            logger.info("send_slack_alert: posted for alert %s", alert.id)
            return True
        else:
            logger.warning(
                "send_slack_alert: status %d for alert %s — %s",
                response.status_code, alert.id, response.text[:200],
            )
            return False

    except Exception as exc:
        logger.error("send_slack_alert: failed for alert %s — %s", alert.id, exc)
        return False


# ---------------------------------------------------------------------------
# Delivery-time dedup / suppression
# ---------------------------------------------------------------------------

def should_suppress_alert(
    db: Session,
    region: str,
    fuel_type: str,
    severity: str,
    suppression_hours: int = 24,
) -> bool:
    """Check if a matching alert was already *delivered* within the suppression window.

    CRITICAL: This filters on notified=True only. If we queried all alerts
    regardless of notified status, an undelivered alert (notified=False) would
    suppress its own delivery attempt — creating a deadlock where the alert
    silently never sends.

    This is the **delivery-time** dedup layer. It is distinct from the
    **creation-time** dedup in anomaly_detection.run_anomaly_detection(),
    which prevents duplicate Alert records from being inserted.

    Args:
        db: Active database session.
        region: State/region code (e.g. "IL").
        fuel_type: Fuel type string (e.g. "electricity").
        severity: Severity level string ("warning" or "critical").
        suppression_hours: How many hours to look back (default 24).

    Returns:
        True if a matching alert was already notified within the window.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=suppression_hours)

    recent = (
        db.query(Alert)
        .filter(
            Alert.notified == True,  # noqa: E712 — intentional SQLAlchemy filter
            Alert.region == region,
            Alert.fuel_type == fuel_type,
            Alert.severity == severity,
            Alert.triggered_at >= cutoff,
        )
        .first()
    )
    return recent is not None


# ---------------------------------------------------------------------------
# Delivery orchestration
# ---------------------------------------------------------------------------

def deliver_pending_alerts(db: Session) -> dict:
    """Deliver all unnotified alerts via email and/or Slack.

    For each Alert with notified=False (ordered by triggered_at):
      1. Look up associated AlertConfig for per-config email/Slack settings.
      2. Fall back to global .env settings (ALERT_DEFAULT_EMAIL, ALERT_SLACK_WEBHOOK).
      3. Attempt email delivery if an address is configured.
      4. Attempt Slack delivery if a webhook URL is configured.
      5. If at least one channel succeeded → mark notified=True, set notified_at.
      6. If all channels failed → leave notified=False (retried next cycle).

    Returns:
        Summary dict with counts of processed, sent, and failed alerts.
    """
    pending = (
        db.query(Alert)
        .filter(Alert.notified == False)  # noqa: E712
        .order_by(Alert.triggered_at)
        .all()
    )

    alerts_processed = 0
    email_sent = 0
    slack_sent = 0
    delivery_failures = 0

    for alert in pending:
        alerts_processed += 1

        # Look up the associated config (may have been soft-deleted)
        config = (
            db.query(AlertConfig)
            .filter(AlertConfig.id == alert.alert_config_id)
            .first()
        )

        # Resolve email recipient: config-level → global default
        email_recipient = None
        if config and config.email:
            email_recipient = config.email
        elif settings.alert_default_email:
            email_recipient = settings.alert_default_email

        # Resolve Slack webhook: config-level → global default
        webhook_url = None
        if config and config.slack_webhook:
            webhook_url = config.slack_webhook
        elif settings.alert_slack_webhook:
            webhook_url = settings.alert_slack_webhook

        email_ok = False
        slack_ok = False

        # Attempt email delivery
        if email_recipient:
            # Build a lightweight config-like object if none exists
            if config is None:
                config = AlertConfig(email=email_recipient)
            elif not config.email:
                config.email = email_recipient
            email_ok = send_email_alert(alert, config)
            if email_ok:
                email_sent += 1

        # Attempt Slack delivery
        if webhook_url:
            slack_ok = send_slack_alert(alert, webhook_url)
            if slack_ok:
                slack_sent += 1

        # Mark delivered if at least one channel succeeded
        if email_ok or slack_ok:
            alert.notified = True
            alert.notified_at = datetime.now(timezone.utc)
            logger.info("deliver_pending_alerts: alert %s marked as notified", alert.id)
        else:
            delivery_failures += 1
            logger.warning(
                "deliver_pending_alerts: all channels failed for alert %s — will retry",
                alert.id,
            )

    db.commit()

    summary = {
        "alerts_processed": alerts_processed,
        "email_sent": email_sent,
        "slack_sent": slack_sent,
        "delivery_failures": delivery_failures,
    }
    logger.info("deliver_pending_alerts: %s", summary)
    return summary
