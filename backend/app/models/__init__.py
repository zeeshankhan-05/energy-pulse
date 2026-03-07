"""Model registry – import everything here so Alembic autogenerate finds all tables."""

from app.models.base import Base, FuelType, Severity, TimestampMixin
from app.models.price_snapshot import PriceSnapshot
from app.models.alert_config import AlertConfig
from app.models.alert import Alert
from app.models.market_summary import MarketSummary

__all__ = [
    "Base",
    "FuelType",
    "Severity",
    "TimestampMixin",
    "PriceSnapshot",
    "AlertConfig",
    "Alert",
    "MarketSummary",
]
