import uuid
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Uuid

from app.models.base import Base, TimestampMixin

class MarketSummary(Base, TimestampMixin):
    """Stores AI-generated market summaries for a region based on pricing and anomaly data."""

    __tablename__ = "market_summaries"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Two-letter state code, e.g. "IL"
    region: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    # The actual summarized narrative text
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)

    # MD5 hash of the factual inputs used to generate this summary.
    # Used to short-circuit redundant LLM calls when data hasn't changed.
    data_hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<MarketSummary {self.region} hash={self.data_hash}>"
