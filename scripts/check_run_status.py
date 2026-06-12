import asyncio
import os
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv
load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool

async def main():
    await init_pools()
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text, run_type, status, started_at FROM analysis_runs WHERE status = 'running' LIMIT 1"
        )
        print("Active run:", row)
        recent = await conn.fetch(
            "SELECT id::text, run_type, status, started_at, finished_at FROM analysis_runs ORDER BY started_at DESC LIMIT 5"
        )
        for r in recent:
            print(dict(r))
    await close_pools()

asyncio.run(main())
