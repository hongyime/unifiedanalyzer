import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool
from src.pipeline.incremental_runner import run_incremental


async def main():
    await init_pools()

    print("=== Running full incremental pipeline (end-to-end) ===")
    stats = await run_incremental()
    print(f"Stats: {stats}")

    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    async with analyzer.acquire() as conn:
        run_row = await conn.fetchrow("""
            SELECT run_type, status, error_message, started_at, finished_at
            FROM analysis_runs ORDER BY started_at DESC LIMIT 1
        """)
        print(f"\nLatest analysis_runs row: {dict(run_row) if run_row else None}")

        communities = await conn.fetchval(
            "SELECT COUNT(DISTINCT metadata->>'community_id') FROM behavioral_profiles WHERE metadata ? 'community_id'"
        )
        print(f"Entities with community_id set: {communities}")

        same_person = await conn.fetchval(
            "SELECT COUNT(*) FROM entity_relationships WHERE relationship_type = 'same_person_probability'"
        )
        print(f"same_person_probability relationships: {same_person}")

    print("\n=== Phase 4E readiness check (Strava GPS backfill) ===")
    async with collector.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM strava_activities")
        with_gps = await conn.fetchval("SELECT COUNT(*) FROM strava_activities WHERE start_latlng IS NOT NULL")
        print(f"strava_activities total: {total}")
        print(f"strava_activities with start_latlng set: {with_gps}")

    await close_pools()


asyncio.run(main())
