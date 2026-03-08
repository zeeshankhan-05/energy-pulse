from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis

from app.database import get_async_db
from app.models.market_summary import MarketSummary
from app.services.summary_service import generate_summary

router = APIRouter(prefix="/api/summary", tags=["Summary"])

def get_redis(request: Request) -> redis.Redis:
    """Dependency to retrieve the global Redis connection pool from app state."""
    return request.app.state.redis

@router.get("/{region}")
async def get_latest_summary(
    region: str, 
    db: AsyncSession = Depends(get_async_db)
):
    """Return the most recently generated market summary for the given region."""
    query = (
        select(MarketSummary)
        .filter(MarketSummary.region == region.upper())
        .order_by(MarketSummary.created_at.desc())
        .limit(1)
    )
    result = await db.execute(query)
    summary = result.scalar_one_or_none()
    
    if not summary:
        raise HTTPException(status_code=404, detail=f"No summary found for region {region.upper()}")
        
    return {
        "region": summary.region,
        "summary_text": summary.summary_text,
        "generated_at": summary.created_at,
        "data_changed": False # Can be augmented if frontend relies on "stale data" flag logic
    }

@router.post("/{region}/refresh")
async def refresh_summary(
    region: str,
    db: AsyncSession = Depends(get_async_db),
    redis_client: redis.Redis = Depends(get_redis)
):
    """
    Force-regenerate a market summary for the given region.
    Rate limited to 1 call per 10 minutes per region.
    """
    rate_limit_key = f"rate_limit:summary_refresh:{region.upper()}"
    
    # Check rate limit existance and atomically set it for 10 minutes (600s) if it doesn't
    acquired = await redis_client.set(rate_limit_key, "1", ex=600, nx=True)
    if not acquired:
        raise HTTPException(
            status_code=429, 
            detail="Rate limit exceeded: Please wait 10 minutes before forcing another refresh."
        )
        
    # Call the exact same generate_summary service, bypassing cache
    new_summary = await generate_summary(region, db, bypass_cache=True)
    
    if not new_summary:
        # If generation silently returned None due to missing underlying data
        # clear the lock so they can try again once data arrives
        await redis_client.delete(rate_limit_key)
        raise HTTPException(status_code=400, detail=f"Cannot generate summary: No underlying market data found for {region.upper()}.")
        
    return {
        "region": new_summary.region,
        "summary_text": new_summary.summary_text,
        "generated_at": new_summary.created_at,
        "data_changed": True
    }
