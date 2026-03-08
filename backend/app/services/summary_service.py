import os
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from anthropic import AsyncAnthropic

from app.models.price_snapshot import PriceSnapshot
from app.models.alert import Alert
from app.models.market_summary import MarketSummary


async def generate_summary(region: str, db: AsyncSession) -> MarketSummary | None:
    """
    Generate an AI market summary for a given region using Anthropic's Claude.
    Returns early if the data hasn't changed since the last generated summary.
    """
async def _gather_summary_inputs(region: str, db: AsyncSession):
    """
    Helper function to gather the exact same inputs (latest snapshots, averages, active alerts)
    used for both generating the summary and determining if regeneration is needed.
    """
    # Fetch latest snapshot per fuel type for the region
    latest_snapshot_query = (
        select(PriceSnapshot)
        .filter(PriceSnapshot.region == region.upper())
        .order_by(PriceSnapshot.created_at.desc())
        .limit(10)
    )
    snapshot_result = await db.execute(latest_snapshot_query)
    snapshots = snapshot_result.scalars().all()
    
    latest_by_fuel = {}
    for s in snapshots:
        ft_val = s.fuel_type.value if hasattr(s.fuel_type, "value") else str(s.fuel_type)
        if ft_val not in latest_by_fuel:
            latest_by_fuel[ft_val] = s

    if not latest_by_fuel:
        return None, None, None, None

    # Fetch 30-day rolling averages
    cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)
    avg_query = (
        select(PriceSnapshot)
        .filter(
            PriceSnapshot.region == region.upper(),
            PriceSnapshot.created_at >= cutoff_30d,
            PriceSnapshot.price.isnot(None)
        )
    )
    avg_result = await db.execute(avg_query)
    recent_prices = avg_result.scalars().all()
    
    averages = {}
    for ft, current_snap in latest_by_fuel.items():
        ft_prices = [float(p.price) for p in recent_prices if getattr(p.fuel_type, "value", str(p.fuel_type)) == ft]
        if ft_prices:
            averages[ft] = sum(ft_prices) / len(ft_prices)

    # Fetch active alerts in the last 24 hours
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    alert_query = (
        select(Alert)
        .filter(
            Alert.region == region.upper(),
            Alert.created_at >= cutoff_24h
        )
    )
    alert_result = await db.execute(alert_query)
    active_alerts = alert_result.scalars().all()

    # Hash Data ensuring we don't call Claude repeatedly for the same inputs
    hash_inputs = [region.upper()]
    for ft, s in sorted(latest_by_fuel.items()):
        hash_inputs.append(f"{ft}:{s.price}")
    for a in sorted(active_alerts, key=lambda x: str(x.id)):
        hash_inputs.append(str(a.id))
    
    hash_str = "-".join(hash_inputs)
    data_hash = hashlib.md5(hash_str.encode("utf-8")).hexdigest()

    return latest_by_fuel, averages, active_alerts, data_hash


async def should_regenerate(region: str, db: AsyncSession) -> bool:
    """
    Determine if a new summary should be generated for the region.
    Returns True if:
      - No summary exists.
      - The latest summary is older than 6 hours.
      - Factual data inputs (hash) have changed AND price deviated by >2%.
    """
    # 1. Fetch newest summary
    summary_query = (
        select(MarketSummary)
        .filter(MarketSummary.region == region.upper())
        .order_by(MarketSummary.created_at.desc())
        .limit(1)
    )
    summary_res = await db.execute(summary_query)
    newest = summary_res.scalar_one_or_none()

    if not newest:
        return True

    # 2. Time check (older than 6 hours)
    if not newest.created_at:
        return True # Fallback if missing timestamp somehow
    now_utc = datetime.now(timezone.utc)
    created_utc = newest.created_at.replace(tzinfo=timezone.utc) if newest.created_at.tzinfo is None else newest.created_at
    if (now_utc - created_utc) > timedelta(hours=6):
        return True

    # 3. Hash Check
    inputs = await _gather_summary_inputs(region, db)
    if not inputs or not inputs[0]: # latest_by_fuel
        return False # No data to generate anyway
    
    current_latest_by_fuel, _, _, current_hash = inputs

    if current_hash == newest.data_hash:
        return False # Hash is identical, nothing actually changed in underlying DB rows

    # 4. Price Delta Check (>2% shift compared to when summary was generated)
    # Fetch definitive final price for each fuel type exactly ON or BEFORE the summary was created
    historical_query = (
        select(PriceSnapshot)
        .filter(
            PriceSnapshot.region == region.upper(),
            PriceSnapshot.created_at <= created_utc,
            PriceSnapshot.price.isnot(None)
        )
        .distinct(PriceSnapshot.fuel_type)
        .order_by(PriceSnapshot.fuel_type, PriceSnapshot.created_at.desc())
    )
    hist_result = await db.execute(historical_query)
    historical_snaps = hist_result.scalars().all()
    
    historical_by_fuel = {
        (s.fuel_type.value if hasattr(s.fuel_type, "value") else str(s.fuel_type)): float(s.price)
        for s in historical_snaps
    }

    # Compare current against historical
    for ft, curr_snap in current_latest_by_fuel.items():
        if curr_snap.price is None:
            continue
            
        curr_price = float(curr_snap.price)
        old_price = historical_by_fuel.get(ft)
        
        # If we have a newly introduced fuel type, or it changed heavily
        if old_price is None:
            return True
            
        if old_price > 0:
            delta = abs((curr_price - old_price) / old_price)
            if delta > 0.02:
                return True

    return False


async def generate_summary(region: str, db: AsyncSession, bypass_cache: bool = False) -> MarketSummary | None:
    """
    Generate an AI market summary for a given region using Anthropic's Claude.
    If bypass_cache is False, will return early if identical cache exists.
    """
    inputs = await _gather_summary_inputs(region, db)
    if not inputs or not inputs[0]:
        return None
    latest_by_fuel, averages, active_alerts, data_hash = inputs

    # 3. Check for existing summary with this hash (Early Return)
    if not bypass_cache:
        existing_query = select(MarketSummary).filter(
            MarketSummary.region == region.upper(),
            MarketSummary.data_hash == data_hash
        ).limit(1)
        existing_result = await db.execute(existing_query)
        existing_summary = existing_result.scalar_one_or_none()

        if existing_summary:
            return existing_summary

    # 4. Construct Prompt
    prompt_lines = [
        f"You are a leading energy market analyst writing a brief market update for {region.upper()}.",
        "Using the data below, write a 2-3 sentence plain-English market summary in a Bloomberg terminal / financial tone.",
        "Include specific percentages or price points if available.",
        "",
        "LATEST PRICES:"
    ]
    for ft, s in latest_by_fuel.items():
        avg = averages.get(ft)
        avg_text = f"(30-day avg: {avg:.4f} {s.unit})" if avg else ""
        prompt_lines.append(f"- {ft.capitalize()}: {float(s.price) if s.price else 'N/A'} {s.unit} {avg_text}")

    prompt_lines.append("\nRECENT ALERTS (last 24hrs):")
    if active_alerts:
        for alert in active_alerts:
            prompt_lines.append(f"- [{alert.severity}] {alert.message}")
    else:
        prompt_lines.append("- No major anomalies detected.")

    prompt_text = "\n".join(prompt_lines)

    # 5. Call Anthropic API
    api_key = os.getenv("ANTHROPIC_API_KEY")

    try:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set in the environment.")

        client = AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[
                {"role": "user", "content": prompt_text}
            ]
        )
        # Handle differing response structures in Anthropic SDK
        if hasattr(response.content[0], "text"):
            summary_text = response.content[0].text
        else:
            summary_text = str(response.content[0])
    except Exception as e:
        # Graceful fallback if API fails or key is missing
        summary_text = f"Market data summary currently unavailable ({str(e)})."
        # Do NOT save a fallback to the DB with the valid data_hash, so it retries later.
        dummy = MarketSummary(region=region.upper(), summary_text=summary_text, data_hash="error")
        return dummy

    # 6. Save to DB
    new_summary = MarketSummary(
        region=region.upper(),
        summary_text=summary_text,
        data_hash=data_hash
    )
    db.add(new_summary)
    await db.commit()
    await db.refresh(new_summary)

    return new_summary
