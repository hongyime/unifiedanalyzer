"""Inspect collector table schemas for Phase 4 needs."""
import asyncio, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_collector_pool

TABLES = [
    "strava_activities",
    "whatsapp_messages",
    "telegram_messages",
    "instagram_posts",
    "tiktok_posts",
    "lemon8_posts",
    "github_events",
    "youtube_videos",
]

async def main():
    await init_pools()
    collector = get_collector_pool()

    async with collector.acquire() as conn:
        for table in TABLES:
            try:
                cols = await conn.fetch("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = $1
                    ORDER BY ordinal_position
                """, table)
                col_names = [r["column_name"] for r in cols]
                print(f"\n{table}:")
                print(f"  {col_names}")
                # Show row count
                cnt = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                print(f"  rows: {cnt}")
            except Exception as e:
                print(f"\n{table}: ERROR {e}")

    await close_pools()

asyncio.run(main())
