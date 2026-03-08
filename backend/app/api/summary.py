"""API router: AI-style market summaries."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.price_snapshot import PriceSnapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["summary"])


@router.get("/summary/{region}")
def get_market_summary(region: str, db: Session = Depends(get_db)) -> dict:
    """Generate a template-driven market summary for a given region."""
    region_upper = region.upper()
    
    # ------------------------------------------------------------------
    # 1. Fetch data: last 6 months for this region, both fuel types
    # ------------------------------------------------------------------
    # Calculate exactly 6 months ago (naive approximation via days is fine)
    cutoff = datetime.now(timezone.utc) - timedelta(days=6 * 31)
    cutoff_period = cutoff.strftime("%Y-%m")
    
    # Get all matching snapshots
    rows = (
        db.query(PriceSnapshot)
        .filter(
            PriceSnapshot.region == region_upper,
            PriceSnapshot.unit.in_(["$/kWh", "$/MMBtu"]),
            PriceSnapshot.period >= cutoff_period,
        )
        .order_by(PriceSnapshot.period.asc())
        .all()
    )
    
    if not rows:
        raise HTTPException(
            status_code=404, 
            detail=f"No recent data available for region {region_upper}"
        )
        
    # Check freshness of the *most recently created* row among the result
    latest_created = max(r.created_at for r in rows)
    data_changed = latest_created >= datetime.now(timezone.utc) - timedelta(hours=24)
    
    # ------------------------------------------------------------------
    # 2. Compute stats per fuel type
    # ------------------------------------------------------------------
    elec_rows = [r for r in rows if r.fuel_type.value == "electricity"]
    gas_rows = [r for r in rows if r.fuel_type.value == "natural_gas"]
    
    sentences = []
    
    # helper for trend commentary
    def build_commentary(fuel_name: str, stats_rows: list[PriceSnapshot]) -> str:
        if not stats_rows:
            return ""
            
        latest = stats_rows[-1]
        price_val = float(latest.price) if latest.price is not None else 0.0
        
        if len(stats_rows) >= 2:
            first = stats_rows[0]
            first_val = float(first.price) if first.price is not None else 0.0
            
            if first_val > 0:
                pct_change = ((price_val - first_val) / first_val) * 100
                trend = "upward" if pct_change > 0 else "downward"
                impact = "surged" if pct_change > 15 else ("dropped" if pct_change < -15 else "shifted")
                
                return f"{fuel_name} prices have {impact} by {abs(pct_change):.1f}% over the last 6 months, continuing a {trend} trend to reach ${price_val:.4f} {latest.unit}."
                
        return f"{fuel_name} is currently trading at ${price_val:.4f} {latest.unit}."
    
    # Electricity stats
    if elec_rows:
        sentences.append(build_commentary("Electricity", elec_rows))
        
    # Gas stats
    if gas_rows:
        sentences.append(build_commentary("Natural gas", gas_rows))
        
    # Combining the narrative
    if not sentences:
        fallback = f"Market data is available for {region_upper}, but insufficient history exists for a full trend analysis."
        sentences.append(fallback)
        
    summary_text = " ".join(sentences)

    return {
        "region": region_upper,
        "summary_text": summary_text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_changed": data_changed,
    }
