import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool

async def main():
    await init_pools()
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.canonical_name AS name_a, b.canonical_name AS name_b,
                   r.weight, r.sources
            FROM entity_relationships r
            JOIN entities a ON a.id = r.entity_a_id
            JOIN entities b ON b.id = r.entity_b_id
            WHERE r.relationship_type = 'same_person_probability'
            ORDER BY r.weight DESC
        """)
        for r in rows:
            print(f"{r['name_a']!r} <-> {r['name_b']!r} | weight={r['weight']}")
            print(f"  sources={r['sources']}")
    await close_pools()

asyncio.run(main())
