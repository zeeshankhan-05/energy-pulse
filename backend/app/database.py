"""SQLAlchemy engine and session factory for EnergyPulse."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Session:
    """FastAPI dependency that yields a DB session and closes it on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ------------------------------------------------------------------
# Async Setup
# ------------------------------------------------------------------

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Safely replace adapter for asyncpg
async_url = settings.database_url \
    .replace("postgresql://", "postgresql+asyncpg://") \
    .replace("postgresql+psycopg2://", "postgresql+asyncpg://")

async_engine = create_async_engine(
    async_url,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, 
    class_=AsyncSession, 
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def get_async_db() -> AsyncSession:
    """FastAPI dependency that yields an async DB session and closes it on exit."""
    async with AsyncSessionLocal() as session:
        yield session
