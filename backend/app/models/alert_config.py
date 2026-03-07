"""AlertConfig – user-defined thresholds that trigger price alerts."""

import uuid

from sqlalchemy import Boolean, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.models.base import Base, FuelType, TimestampMixin, fuel_type_col


class AlertConfig(Base, TimestampMixin):
    """One row per (region, fuel_type) alert rule configured by a user."""

    __tablename__ = "alert_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    region: Mapped[str] = mapped_column(String(10), nullable=False)

    fuel_type: Mapped[FuelType] = mapped_column(fuel_type_col, nullable=False)

    # e.g. 15.00 → fire if current price is ≥ 15 % above the rolling average
    threshold_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)

    # At least one notification target must be set at the application level,
    # but both are nullable so either channel can be omitted.
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slack_webhook: Mapped[str | None] = mapped_column(String(512), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    alerts: Mapped[list["Alert"]] = relationship(  # noqa: F821
        "Alert", back_populates="alert_config", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<AlertConfig {self.region}/{self.fuel_type} "
            f"threshold={self.threshold_pct}% active={self.is_active}>"
        )
