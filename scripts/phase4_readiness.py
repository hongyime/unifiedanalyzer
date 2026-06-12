"""Check data availability for each Phase 4 option."""
import asyncio, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool

async def main():
    await init_pools()
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    print("=== Phase 4A: Location inference (Strava GPS) ===")
    async with collector.acquire() as conn:
        # Check if start_latlng is stored
        try:
            gps_count = await conn.fetchval(
                "SELECT COUNT(*) FROM strava_activities WHERE start_latlng IS NOT NULL AND start_latlng != ''"
            )
            print(f"  Strava activities with start_latlng: {gps_count}")
            if gps_count > 0:
                sample = await conn.fetch(
                    "SELECT start_latlng FROM strava_activities WHERE start_latlng IS NOT NULL LIMIT 3"
                )
                for r in sample:
                    print(f"  sample: {r['start_latlng']}")
        except Exception as e:
            print(f"  start_latlng check: {e}")

        try:
            tz_count = await conn.fetchval(
                "SELECT COUNT(*) FROM strava_activities WHERE timezone IS NOT NULL"
            )
            print(f"  Strava activities with timezone: {tz_count}")
        except Exception as e:
            print(f"  timezone check: {e}")

    print("\n=== Phase 4B: Content fingerprinting ===")
    async with collector.acquire() as conn:
        for table, col in [
            ("instagram_posts", "caption"),
            ("tiktok_posts", "description"),
            ("lemon8_posts", "content"),
            ("youtube_videos", "description"),
            ("telegram_messages", "content"),
            ("whatsapp_messages", "content"),
        ]:
            try:
                cnt = await conn.fetchval(
                    f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} != ''"
                )
                print(f"  {table}.{col}: {cnt} rows")
            except Exception as e:
                print(f"  {table}: {e}")

    print("\n=== Phase 4C: Temporal correlation ===")
    async with analyzer.acquire() as conn:
        event_count = await conn.fetchval("SELECT COUNT(*) FROM timeline_events")
        entity_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT entity_id) FROM timeline_events WHERE entity_id IS NOT NULL"
        )
        source_dist = await conn.fetch(
            "SELECT source, COUNT(*) as cnt FROM timeline_events GROUP BY source ORDER BY cnt DESC"
        )
        print(f"  timeline_events: {event_count} total, {entity_count} entities with events")
        for r in source_dist:
            print(f"    {r['source']}: {r['cnt']}")

        # Time span
        span = await conn.fetchrow(
            "SELECT MIN(occurred_at) AS oldest, MAX(occurred_at) AS newest FROM timeline_events"
        )
        if span["oldest"]:
            print(f"  Time span: {span['oldest'].date()} to {span['newest'].date()}")

    print("\n=== Phase 4D: Follower/following overlap ===")
    async with collector.acquire() as conn:
        for table in ["instagram_followers", "tiktok_followers", "youtube_subscribers", "github_followers"]:
            try:
                cnt = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                print(f"  {table}: {cnt}")
            except Exception as e:
                print(f"  {table}: not found ({e})")

    print("\n=== Current pipeline health ===")
    async with analyzer.acquire() as conn:
        runs = await conn.fetch("""
            SELECT run_type, status, started_at, finished_at,
                   EXTRACT(EPOCH FROM (finished_at - started_at))::int AS duration_sec,
                   entities_processed, events_created, signals_created, error_message
            FROM analysis_runs
            ORDER BY started_at DESC LIMIT 5
        """)
        for r in runs:
            dur = f"{r['duration_sec']}s" if r['duration_sec'] else "?"
            err = f" ERROR: {r['error_message'][:60]}" if r['error_message'] else ""
            print(f"  {r['run_type']} {r['status']} {dur} — {r['entities_processed']}e {r['events_created']}ev {r['signals_created']}sig{err}")

    await close_pools()

asyncio.run(main())
