"""Debug bio mentions — show all mentions found in bios and check against known usernames."""
import asyncio
import os
import sys
os.chdir(r"C:\unifiedanalyzer")
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(r"C:\unifiedanalyzer\.env")

from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool
from src.pipeline.bio_mention import BIO_SOURCES, _normalize_mention, _extract_mentions

async def main():
    await init_pools()
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    # Build all normalized known usernames
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id, platform_username FROM entity_platform_links WHERE platform_username IS NOT NULL"
        )
        username_to_entities = {}
        for l in links:
            norm = _normalize_mention(l["platform_username"])
            if norm:
                username_to_entities.setdefault(norm, []).append((l["entity_id"], l["source"]))

        pid_to_entity = {}
        all_links = await conn.fetch("SELECT entity_id::text, source, platform_id FROM entity_platform_links")
        for l in all_links:
            pid_to_entity[(l["source"], l["platform_id"])] = l["entity_id"]

    print(f"Known normalized usernames: {len(username_to_entities)}")
    print(f"Sample: {list(username_to_entities.keys())[:20]}")

    # Collect all mentions and their match status
    all_mentions = set()
    matches = []

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
                        all_mentions.add(m)
                        if m in username_to_entities:
                            for target_eid, target_src in username_to_entities[m]:
                                if source_eid and target_eid != source_eid:
                                    matches.append({
                                        "source": source, "pid": bio_pid[:20],
                                        "source_eid": (source_eid or "none")[:8],
                                        "mention": m, "target_eid": target_eid[:8],
                                        "target_src": target_src,
                                    })
            except Exception as e:
                print(f"{source}: ERROR {e}")

    print(f"\nTotal unique mentions extracted: {len(all_mentions)}")
    print(f"Sample mentions: {sorted(all_mentions)[:30]}")

    # Check overlap
    overlap = all_mentions & set(username_to_entities.keys())
    print(f"\nMentions that match known usernames: {len(overlap)}")
    if overlap:
        print(f"  Matching: {sorted(overlap)[:20]}")

    print(f"\nMatches with cross-entity check: {len(matches)}")
    for m in matches[:20]:
        print(f"  {m}")

    # Also show bios without entity links (can't produce matches)
    unlinked = 0
    async with collector.acquire() as conn:
        for source, query in BIO_SOURCES:
            try:
                rows = await conn.fetch(query)
                for r in rows:
                    if r["bio"] and "@" in r["bio"]:
                        bio_pid = str(r["pid"])
                        if not pid_to_entity.get((source, bio_pid)):
                            unlinked += 1
            except Exception:
                pass
    print(f"\nBios with @ but NO entity link: {unlinked}")

    await close_pools()

asyncio.run(main())
