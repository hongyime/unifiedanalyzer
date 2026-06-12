import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool

async def main():
    await init_pools()
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.canonical_name, bp.total_events, bp.posting_hour_dist,
                   pg_typeof(bp.posting_hour_dist) AS dist_type
            FROM behavioral_profiles bp
            JOIN entities e ON e.id = bp.entity_id
            WHERE bp.posting_hour_dist IS NOT NULL
            ORDER BY bp.total_events DESC NULLS LAST
        """)
        print(f"Profiles with posting_hour_dist: {len(rows)}")
        for r in rows:
            dist = r['posting_hour_dist']
            print(f"  {r['canonical_name']!r} total_events={r['total_events']} dist_type={r['dist_type']} dist={str(dist)[:60]}")

        # Check strava_athletes country data
        print("\n=== Strava athlete location (in analyzer) ===")
        # Check via collector
    collector = get_collector_pool()
    async with collector.acquire() as conn:
        cnt = await conn.fetchval("SELECT COUNT(*) FROM strava_athletes WHERE country IS NOT NULL AND country != ''")
        sample = await conn.fetch("SELECT platform_athlete_id::text, city, state, country FROM strava_athletes WHERE country IS NOT NULL AND country != '' LIMIT 5")
        print(f"Strava athletes with country: {cnt}")
        for r in sample:
            print(f"  pid={r['platform_athlete_id']} city={r['city']} country={r['country']}")

    # Check if any strava platform_ids are in entity_platform_links
    async with pool.acquire() as conn:
        strava_links = await conn.fetchval("SELECT COUNT(*) FROM entity_platform_links WHERE source='strava'")
        print(f"\nStrava entity_platform_links: {strava_links}")
        if strava_links:
            links = await conn.fetch("SELECT platform_id FROM entity_platform_links WHERE source='strava' LIMIT 5")
            for l in links: print(f"  platform_id={l['platform_id']}")

    await close_pools()

asyncio.run(main())
