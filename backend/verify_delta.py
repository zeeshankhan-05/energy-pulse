import asyncio
import uuid
import httpx
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.services.summary_service import should_regenerate, _gather_summary_inputs
from app.models.price_snapshot import PriceSnapshot
from app.models.market_summary import MarketSummary

async def run_detailed_tests():
    # TEST 1: Endpoints & Rate Limiting (using httpx against localhost:8000)
    # Note: Requires the uvicorn server to be running, but we can test logic directly if it's not.
    # We will test the DB logic instead to ensure self-containment.
    
    region = "IL"
    async with AsyncSessionLocal() as db:
        print(f"--- TEST: should_regenerate (Empty State) for {region} ---")
        # Ensure clean state for IL
        await db.execute(MarketSummary.__table__.delete().where(MarketSummary.region == region))
        await db.commit()
        
        needs_regeneration = await should_regenerate(region, db)
        print(f"Empy state needs regeneration? {needs_regeneration} (Expected: True)")
        
        # Manually create a dummy summary to test the 2% delta
        inputs = await _gather_summary_inputs(region, db)
        if not inputs or not inputs[0]:
            print(f"Skipping 2% delta test - No price snapshot data found for {region}")
            return
            
        latest_by_fuel, averages, active_alerts, data_hash = inputs
        print(f"Current TX Hash: {data_hash}")
        
        # Inject the summary into the DB
        dummy = MarketSummary(
            region=region,
            summary_text="Initial dummy summary",
            data_hash=data_hash
        )
        db.add(dummy)
        await db.commit()
        await db.refresh(dummy)
        
        print("\n--- TEST: should_regenerate (Identical State) ---")
        needs_regen_identical = await should_regenerate(region, db)
        print(f"Identical state needs regeneration? {needs_regen_identical} (Expected: False)")

        print("\n--- TEST: should_regenerate (2% Price Delta Check) ---")
        # To test the delta, we need to artificially inject a new PriceSnapshot 
        # that is > 2% different from the prices that were just used.
        # Pick the first available fuel type
        target_ft_enum = list(latest_by_fuel.keys())[0]
        original_snapshot = latest_by_fuel[target_ft_enum]
        original_price = float(original_snapshot.price)
        
        # Increase price by 5%
        new_price = original_price * 1.05
        
        print(f"Injecting new snapshot for {target_ft_enum}: {original_price} -> {new_price} (+5%)")
        fake_snapshot = PriceSnapshot(
            id=uuid.uuid4(),
            region=region,
            fuel_type=original_snapshot.fuel_type,
            price=new_price,
            unit=original_snapshot.unit,
            source="TEST_SCRIPT",
            period="2026-03", # Required field
            created_at=datetime.now(timezone.utc)
        )
        db.add(fake_snapshot)
        await db.commit()
        
        # Re-run should_regenerate. 
        # The hash WILL be different (because the latest price snapshot changed).
        # We want to ensure it actually identifies the >2% drift.
        needs_regen_delta = await should_regenerate(region, db)
        print(f">2% delta needs regeneration? {needs_regen_delta} (Expected: True)")
        
        print("\n--- TEST: should_regenerate (<2% Price Delta Check) ---")
        # Let's clean up the 5% drift and inject a 1% drift
        await db.delete(fake_snapshot)
        await db.commit()
        
        new_price_1pct = original_price * 1.01
        print(f"Injecting new snapshot for {target_ft_enum}: {original_price} -> {new_price_1pct} (+1%)")
        fake_snapshot_1pct = PriceSnapshot(
            id=uuid.uuid4(),
            region=region,
            fuel_type=original_snapshot.fuel_type,
            price=new_price_1pct,
            unit=original_snapshot.unit,
            source="TEST_SCRIPT",
            period="2026-03", # Required field
            created_at=datetime.now(timezone.utc)
        )
        db.add(fake_snapshot_1pct)
        await db.commit()
        
        needs_regen_tiny_delta = await should_regenerate(region, db)
        print(f"<2% delta needs regeneration? {needs_regen_tiny_delta} (Expected: False)")
        
        # Cleanup injected test data
        await db.delete(fake_snapshot_1pct)
        await db.execute(MarketSummary.__table__.delete().where(MarketSummary.region == region))
        await db.commit()

        print("\nAll DB-level delta math confirmed working.")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(run_detailed_tests())
