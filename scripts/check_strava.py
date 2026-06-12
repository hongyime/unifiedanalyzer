import asyncio
from dotenv import load_dotenv
load_dotenv()
from src.db.connection import init_pools, close_pools, get_collector_pool


async def main():
    await init_pools()
    c = get_collector_pool()
    async with c.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, platform_athlete_id, username FROM strava_athletes LIMIT 3"
        )
        for r in rows:
            print(f"  db_id={r['id']} platform_id={r['platform_athlete_id']} username={r['username']}")

        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='strava_activities' ORDER BY ordinal_position"
        )
        print("strava_activities cols:", [r["column_name"] for r in cols])

        sample = await conn.fetch(
            "SELECT athlete_id FROM strava_activities LIMIT 3"
        )
        print("sample activity athlete_ids:", [r["athlete_id"] for r in sample])
    await close_pools()


asyncio.run(main())
