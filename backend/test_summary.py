import asyncio
import os
from dotenv import load_dotenv

# Load env before importing DB or service
load_dotenv()

from app.database import AsyncSessionLocal
from app.services.summary_service import generate_summary

async def main():
    async with AsyncSessionLocal() as db:
        print("Fetching summary for IL...")
        summary1 = await generate_summary("IL", db)
        if summary1:
            print(f"Summary 1 Hash: {summary1.data_hash}")
            print(f"Summary 1 Text: {summary1.summary_text}")
        else:
            print("No data found for IL.")
        
        print("\nFetching summary for IL again to test MD5 cache...")
        summary2 = await generate_summary("IL", db)
        if summary2:
            print(f"Summary 2 Hash: {summary2.data_hash}")
            print(f"Summary 2 Text: {summary2.summary_text}")
        
        # Verify if they are the exact same object (from the DB, it should be)
        if summary1 and summary2:
            print(f"\nCache Hit Matched? {summary1.id == summary2.id}")

if __name__ == "__main__":
    asyncio.run(main())
