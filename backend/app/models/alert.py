"""Alert – a fired price-spike event linked to an AlertConfig."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.models.base import Base, FuelType, Severity, TimestampMixin, fuel_type_col, severity_col


class Alert(Base, TimestampMixin):
    """One row per threshold-crossing event that was detected."""

    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    alert_config_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("alert_configs.id", ondelete="CASCADE"),
        nullable=False,
    )

    region: Mapped[str] = mapped_column(String(10), nullable=False)

    fuel_type: Mapped[FuelType] = mapped_column(fuel_type_col, nullable=False)

    severity: Mapped[Severity] = mapped_column(severity_col, nullable=False)

    current_price: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)

    rolling_avg_price: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)

    # Positive = above average, negative = below
    deviation_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)

    message: Mapped[str] = mapped_column(Text, nullable=False)

    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Flipped to True once email/Slack has been sent so we don't re-notify
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    alert_config: Mapped["AlertConfig"] = relationship(  # noqa: F821
        "AlertConfig", back_populates="alerts", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f"<Alert {self.severity} {self.region}/{self.fuel_type} "
            f"dev={self.deviation_pct}% notified={self.notified}>"
        )
