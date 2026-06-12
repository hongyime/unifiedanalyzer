import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool
from src.api.routes.intelligence import get_intelligence


async def main():
    await init_pools()

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text FROM entities WHERE canonical_name = $1", "Natasha Chang"
        )

    if not row:
        print("Entity 'Natasha Chang' not found")
        await close_pools()
        return

    entity_id = row["id"]
    print(f"entity_id = {entity_id}")

    result = await get_intelligence(entity_id)
    print(json.dumps(result, indent=2, default=str))

    await close_pools()


asyncio.run(main())
