from pydantic_settings import BaseSettings
from pydantic import model_validator


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:password@localhost:5432/energypulse"
    redis_url: str = "redis://localhost:6379"
    eia_api_key: str = ""
    anthropic_api_key: str = ""
    sendgrid_api_key: str = ""

    # Legacy key kept for backward compatibility with existing Docker/.env setups.
    # New code should use alert_slack_webhook; if unset, it falls back to this.
    slack_webhook_url: str = ""

    # Alert delivery settings
    alert_from_email: str = "alerts@energypulse.app"
    alert_default_email: str = ""
    alert_slack_webhook: str = ""

    @model_validator(mode="after")
    def _backfill_slack_webhook(self) -> "Settings":
        """Fall back to the legacy SLACK_WEBHOOK_URL when ALERT_SLACK_WEBHOOK is empty."""
        if not self.alert_slack_webhook and self.slack_webhook_url:
            self.alert_slack_webhook = self.slack_webhook_url
        return self

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()
