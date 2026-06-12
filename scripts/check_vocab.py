import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool

NAMES = ['Justin', 'Kokonuttree', 'Natasha Chang', 'vi ☻ ']

async def main():
    await init_pools()
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    async with analyzer.acquire() as conn:
        print("Vocab profiles for similarity signal entities:")
        for name in NAMES:
            row = await conn.fetchrow("""
                SELECT bp.metadata->'content_fingerprint' AS fp,
                       epl.source
                FROM behavioral_profiles bp
                JOIN entities e ON e.id = bp.entity_id
                LEFT JOIN entity_platform_links epl ON epl.entity_id = e.id
                WHERE e.canonical_name = $1
                LIMIT 1
            """, name)
            if row:
                fp = row['fp']
                if isinstance(fp, str):
                    fp = json.loads(fp)
                if fp:
                    print(f"  {name!r}: vocab={fp.get('vocab_size')} tokens={fp.get('token_count')} posts={fp.get('post_count')} avg_words={fp.get('avg_words_per_post')} richness={fp.get('vocab_richness')}")
                    print(f"    top_words: {fp.get('top_words', [])[:8]}")

        # Check Natasha Chang and vi ☻ platform sources
        print("\nPlatform sources:")
        for name in NAMES:
            rows = await conn.fetch("""
                SELECT epl.source, epl.platform_id
                FROM entities e
                JOIN entity_platform_links epl ON epl.entity_id = e.id
                WHERE e.canonical_name = $1
            """, name)
            sources = [(r['source'], r['platform_id']) for r in rows]
            print(f"  {name!r}: {sources}")

    await close_pools()

asyncio.run(main())
