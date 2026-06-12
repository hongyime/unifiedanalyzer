import asyncio
import os
import sys
import logging
os.chdir(r"C:\unifiedanalyzer")
# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(r"C:\unifiedanalyzer\.env")
logging.basicConfig(level=logging.INFO)

from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool
from src.pipeline.bio_mention import detect_bio_mentions, BIO_SOURCES, _normalize_mention, _extract_mentions

async def main():
    await init_pools()

    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    print("=== Checking bio sources ===")
    sample_mentions = []
    async with collector.acquire() as conn:
        for source, query in BIO_SOURCES:
            try:
                rows = await conn.fetch(query)
                with_at = [r for r in rows if r["bio"] and "@" in r["bio"]]
                print(f"  {source}: {len(rows)} bios, {len(with_at)} contain @")
                for r in with_at[:2]:
                    mentions = _extract_mentions(r["bio"])
                    if mentions:
                        sample_mentions.append((source, str(r["pid"]), mentions))
                        print(f"    pid={str(r['pid'])[:20]} mentions={mentions[:5]}")
            except Exception as e:
                print(f"  {source}: ERROR {e}")

    print("\n=== Entity platform usernames (sample) ===")
    async with analyzer.acquire() as conn:
        total_links = await conn.fetchval(
            "SELECT COUNT(*) FROM entity_platform_links WHERE platform_username IS NOT NULL"
        )
        print(f"Total links with username: {total_links}")

        # Show normalized usernames
        links = await conn.fetch(
            "SELECT source, platform_username FROM entity_platform_links WHERE platform_username IS NOT NULL LIMIT 20"
        )
        norm_usernames = set()
        for l in links:
            norm = _normalize_mention(l["platform_username"])
            if norm:
                norm_usernames.add(norm)
                print(f"  {l['source']} raw={l['platform_username']!r} norm={norm!r}")

        # Check if any sample mention matches a known username
        print("\n=== Cross-matching mentions vs known usernames ===")
        all_links = await conn.fetch(
            "SELECT source, platform_username FROM entity_platform_links WHERE platform_username IS NOT NULL"
        )
        all_norms = set()
        for l in all_links:
            norm = _normalize_mention(l["platform_username"])
            if norm:
                all_norms.add(norm)
        print(f"Total normalized known usernames: {len(all_norms)}")

        for source, pid, mentions in sample_mentions[:5]:
            for m in mentions:
                if m in all_norms:
                    print(f"  MATCH! {source}/{pid} mentions @{m} which is a known entity")
                else:
                    pass  # no match

    print("\n=== Running detect_bio_mentions ===")
    stats = await detect_bio_mentions()
    print(f"Stats: {stats}")

    await close_pools()

asyncio.run(main())
