import asyncio
from dotenv import load_dotenv
load_dotenv()
from src.db.connection import init_pools, close_pools, get_analyzer_pool


async def main():
    await init_pools()
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        cleaned = await conn.execute("""
            UPDATE analysis_runs
            SET status = 'failed', finished_at = NOW(),
                error_message = 'Manually cleared stale lock'
            WHERE status = 'running'
        """)
        print(f"Cleared: {cleaned}")
    await close_pools()


asyncio.run(main())
