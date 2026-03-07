"""PriceSnapshot – one row per source/region/fuel_type/period combination."""

import uuid

from sqlalchemy import Index, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import mapped_column, Mapped
from sqlalchemy import Uuid

# Use JSONB on PostgreSQL; fall back to plain JSON (stored as TEXT) on SQLite
# so that unit tests using in-memory SQLite work without modification.
_jsonb_type = JSON().with_variant(JSONB(), "postgresql")

from app.models.base import Base, FuelType, TimestampMixin, fuel_type_col


class PriceSnapshot(Base, TimestampMixin):
    """Stores point-in-time energy prices fetched from external sources."""

    __tablename__ = "price_snapshots"

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        # server_default set via Alembic migration: gen_random_uuid()
    )

    # Who published the data: "EIA", "IL_PUC", "TX_PUC", …
    source: Mapped[str] = mapped_column(String(50), nullable=False)

    # Two-letter state code, e.g. "IL"
    region: Mapped[str] = mapped_column(String(10), nullable=False)

    fuel_type: Mapped[FuelType] = mapped_column(fuel_type_col, nullable=False)

    price: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    # "cents/kWh", "$/MMBtu", …
    unit: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Monthly granularity: "2024-11"
    period: Mapped[str] = mapped_column(String(7), nullable=False)

    # Full API response blob for debugging / reprocessing
    raw_data: Mapped[dict | None] = mapped_column(_jsonb_type, nullable=True)

    # ------------------------------------------------------------------
    # Constraints & indexes
    # ------------------------------------------------------------------

    __table_args__ = (
        UniqueConstraint(
            "source", "region", "fuel_type", "period",
            name="uq_price_snapshot_source_region_fuel_period",
        ),
        Index("ix_price_snapshots_region_fuel_period", "region", "fuel_type", "period"),
    )

    def __repr__(self) -> str:
        return (
            f"<PriceSnapshot {self.source} {self.fuel_type} "
            f"{self.region} {self.period} price={self.price} {self.unit}>"
        )
