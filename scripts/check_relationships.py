import asyncio, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool

async def main():
    await init_pools()
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        types = await conn.fetch("SELECT relationship_type, COUNT(*) as cnt FROM entity_relationships GROUP BY relationship_type ORDER BY cnt DESC")
        print("Relationship types:")
        for r in types:
            print(f"  {r['relationship_type']}: {r['cnt']}")
        total = await conn.fetchval("SELECT COUNT(*) FROM entity_relationships")
        print(f"Total: {total}")
    await close_pools()

asyncio.run(main())
