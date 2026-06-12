"""Debug why WhatsApp group graph still has 0 relationships despite lid_map being populated."""
import asyncio
import os
import sys
os.chdir(r"C:\unifiedanalyzer")
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv(r"C:\unifiedanalyzer\.env")

from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool

async def main():
    await init_pools()
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    # What does entity_platform_links have for WhatsApp?
    async with analyzer.acquire() as conn:
        wa_links = await conn.fetchval(
            "SELECT COUNT(*) FROM entity_platform_links WHERE source = 'whatsapp'"
        )
        print(f"entity_platform_links for whatsapp: {wa_links}")

        if wa_links > 0:
            sample = await conn.fetch(
                "SELECT platform_id, platform_username FROM entity_platform_links WHERE source = 'whatsapp' LIMIT 10"
            )
            print("Sample platform_ids:")
            for r in sample:
                print(f"  platform_id={r['platform_id']} username={r['platform_username']}")

    # What does group_graph.py see after lid resolution?
    async with collector.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.platform_user_id
            FROM whatsapp_messages m
            JOIN whatsapp_chats c ON m.chat_id = c.id
            JOIN whatsapp_users u ON m.sender_id = u.id
            WHERE c.is_group = true AND m.sender_id IS NOT NULL
        """)
        lid_rows = await conn.fetch("SELECT lid, phone_jid FROM whatsapp_lid_map")
        lid_map = {r["lid"]: r["phone_jid"] for r in lid_rows}

    resolved_puids = set()
    lid_count = 0
    for r in rows:
        puid = r["platform_user_id"]
        if "@lid" in (puid or ""):
            resolved = lid_map.get(puid, puid)
            lid_count += 1
            resolved_puids.add(resolved)
        else:
            resolved_puids.add(puid)

    print(f"\nResolved group sender JIDs: {len(resolved_puids)} unique (lid_resolved={lid_count})")
    print(f"Sample resolved JIDs: {sorted(resolved_puids)[:10]}")

    # How many of these resolved JIDs are in entity_platform_links?
    async with analyzer.acquire() as conn:
        wa_platform_ids = await conn.fetch(
            "SELECT platform_id FROM entity_platform_links WHERE source = 'whatsapp'"
        )
    wa_id_set = {r["platform_id"] for r in wa_platform_ids}
    matched = resolved_puids & wa_id_set
    print(f"\nResolved JIDs that match entity_platform_links: {len(matched)} of {len(resolved_puids)}")
    if matched:
        print(f"  Sample matches: {sorted(matched)[:5]}")
    else:
        print("  NO MATCHES — format mismatch between group sender JIDs and entity platform_ids")
        # Show comparison
        sample_resolved = sorted(resolved_puids)[:5]
        sample_entity = sorted(wa_id_set)[:5]
        print(f"  Resolved sample: {sample_resolved}")
        print(f"  Entity IDs sample: {sample_entity}")

    await close_pools()

asyncio.run(main())
