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
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM identity_signals WHERE signal_type = 'bio_mention'"
        )
        print(f"Total bio_mention signals: {count}")

        rows = await conn.fetch("""
            SELECT
                sig.entity_id::text,
                e1.canonical_name AS source_name,
                sig.source_platform,
                sig.value AS mentioned_handle,
                sig.target_platform,
                sig.target_record_id,
                sig.confidence,
                e2.canonical_name AS target_name
            FROM identity_signals sig
            JOIN entities e1 ON e1.id = sig.entity_id
            LEFT JOIN entity_platform_links epl ON epl.source = sig.target_platform AND epl.platform_id = sig.target_record_id
            LEFT JOIN entities e2 ON e2.id = epl.entity_id
            WHERE sig.signal_type = 'bio_mention'
            ORDER BY sig.confidence DESC
            LIMIT 20
        """)
        for r in rows:
            print(f"  {r['source_name']!r} ({r['source_platform']}) → @{r['mentioned_handle']} → {r['target_name']!r} ({r['target_platform']})")

    await close_pools()

asyncio.run(main())
