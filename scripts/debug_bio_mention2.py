"""Deeper debug: examine each matched mention to understand why all same-entity."""
import asyncio
import os
import sys
os.chdir(r"C:\unifiedanalyzer")
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(r"C:\unifiedanalyzer\.env")

from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool
from src.pipeline.bio_mention import BIO_SOURCES, _normalize_mention, _extract_mentions

KNOWN_MATCHES = ['akeshiiv', 'christophe', 'colinandsamir', 'demasrusli', 'everydaycode',
                 'gracetutty', 'izzybizzyspider', 'jocelynwsl', 'kahyinnn', 'kaitiyoo',
                 'kylenutt', 'lidiyapervak', 'megantanhweewen', 'mryeester', 'nikiivictoria',
                 'notjustbikes', 'philpallen', 'saffronsharpe', 'sookja', 'usamalama']

async def main():
    await init_pools()
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    # Build lookup
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id, platform_username FROM entity_platform_links WHERE platform_username IS NOT NULL"
        )
        username_to_entities = {}
        for l in links:
            norm = _normalize_mention(l["platform_username"])
            if norm:
                username_to_entities.setdefault(norm, []).append((l["entity_id"], l["source"], l["platform_id"]))

        pid_to_entity = {}
        all_links = await conn.fetch("SELECT entity_id::text, source, platform_id FROM entity_platform_links")
        for l in all_links:
            pid_to_entity[(l["source"], l["platform_id"])] = l["entity_id"]

    # Find bios that mention these handles
    print("=== Analyzing all 20 matching mentions ===\n")
    async with collector.acquire() as conn:
        for source, query in BIO_SOURCES:
            try:
                rows = await conn.fetch(query)
                for r in rows:
                    if not r["bio"]:
                        continue
                    bio_pid = str(r["pid"])
                    source_eid = pid_to_entity.get((source, bio_pid))
                    mentions = _extract_mentions(r["bio"])
                    for m in mentions:
                        if m not in KNOWN_MATCHES:
                            continue
                        targets = username_to_entities.get(m, [])
                        for target_eid, target_src, target_pid in targets:
                            same = source_eid == target_eid if source_eid else None
                            print(f"Match: @{m}")
                            print(f"  Bio owner: {source}/{bio_pid[:20]} -> entity={source_eid[:8] if source_eid else 'NONE'}")
                            print(f"  Target:    {target_src}/{target_pid[:20]} -> entity={target_eid[:8]}")
                            print(f"  Same entity: {same}")
                            print(f"  Bio snippet: {r['bio'][:200]!r}")
                            print()
            except Exception as e:
                print(f"{source}: ERROR {e}")

    await close_pools()

asyncio.run(main())
