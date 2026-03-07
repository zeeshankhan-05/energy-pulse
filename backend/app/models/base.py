"""Shared declarative base, timestamp mixin, and domain enums."""

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, declared_attr, Mapped, mapped_column


# ---------------------------------------------------------------------------
# Domain enums (defined once, reused across all models)
# ---------------------------------------------------------------------------

class FuelType(str, enum.Enum):
    electricity = "electricity"
    natural_gas = "natural_gas"


class Severity(str, enum.Enum):
    warning = "warning"
    critical = "critical"


# SQLAlchemy column types for the enums – shared so PostgreSQL creates the
# ENUM type only once per type name.
fuel_type_col = SAEnum(FuelType, name="fuel_type_enum")
severity_col = SAEnum(Severity, name="severity_enum")


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Reusable mixin
# ---------------------------------------------------------------------------

class TimestampMixin:
    """Adds auto-managed created_at / updated_at columns to any model."""

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(timezone.utc),
        )

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(timezone.utc),
            onupdate=lambda: datetime.now(timezone.utc),
        )
