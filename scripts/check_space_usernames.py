import asyncio
from dotenv import load_dotenv
load_dotenv()
from src.db.connection import init_pools, close_pools, get_collector_pool


async def main():
    await init_pools()
    c = get_collector_pool()
    async with c.acquire() as conn:
        # Check how many usernames have spaces per platform
        for table, col in [
            ("instagram_profiles", "username"),
            ("tiktok_profiles", "username"),
            ("telegram_users", "username"),
            ("github_users", "login"),
            ("youtube_channels", "custom_url"),
            ("strava_athletes", "username"),
            ("lemon8_profiles", "username"),
        ]:
            try:
                total = await conn.fetchval(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL")
                with_space = await conn.fetchval(
                    f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} LIKE '% %'"
                )
                print(f"{table}.{col}: {with_space}/{total} contain spaces")
                if with_space > 0:
                    samples = await conn.fetch(
                        f"SELECT {col} FROM {table} WHERE {col} LIKE '% %' LIMIT 3"
                    )
                    for s in samples:
                        print(f"  example: '{s[col]}'")
            except Exception as e:
                print(f"{table}.{col}: error - {e}")
    await close_pools()


asyncio.run(main())
