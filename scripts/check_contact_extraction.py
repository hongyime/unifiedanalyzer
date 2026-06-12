import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool
from src.pipeline.contact_extraction import extract_contacts


async def main():
    await init_pools()

    stats = await extract_contacts()
    print(f"Stats: {stats}")

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        for sig_type in ("email_match", "cross_platform_link"):
            rows = await conn.fetch("""
                SELECT s.entity_id::text, e.canonical_name AS name_a,
                       s.target_record_id, t.canonical_name AS name_b,
                       s.source_platform, s.target_platform, s.value, s.confidence
                FROM identity_signals s
                JOIN entities e ON e.id = s.entity_id
                LEFT JOIN entities t ON t.id::text = s.target_record_id
                WHERE s.signal_type = $1
            """, sig_type)
            print(f"\n=== {sig_type} ({len(rows)}) ===")
            for r in rows:
                print(f"  {r['name_a']!r} <-> {r['name_b']!r} | value={r['value']!r} conf={r['confidence']} ({r['source_platform']}->{r['target_platform']})")

    await close_pools()


asyncio.run(main())
