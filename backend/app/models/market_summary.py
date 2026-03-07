"""MarketSummary – AI-generated narrative stored with a content hash."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Uuid

from app.models.base import Base, FuelType, TimestampMixin, fuel_type_col


class MarketSummary(Base, TimestampMixin):
    """Cached LLM summary for a region/fuel_type.

    ``data_hash`` (MD5 of the input records) lets callers skip regeneration
    when the underlying data has not changed since the last run.
    """

    __tablename__ = "market_summaries"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    region: Mapped[str] = mapped_column(String(10), nullable=False)

    fuel_type: Mapped[FuelType] = mapped_column(fuel_type_col, nullable=False)

    summary_text: Mapped[str] = mapped_column(Text, nullable=False)

    # MD5 of the serialised input data; used to detect stale cache entries
    data_hash: Mapped[str] = mapped_column(String(32), nullable=False)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<MarketSummary {self.region}/{self.fuel_type} "
            f"generated_at={self.generated_at}>"
        )
