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

    async with collector.acquire() as conn:
        lid_count = await conn.fetchval("SELECT COUNT(*) FROM whatsapp_lid_map")
        print(f"whatsapp_lid_map entries: {lid_count}")
        if lid_count > 0:
            sample = await conn.fetch("SELECT lid, phone_jid FROM whatsapp_lid_map LIMIT 5")
            for r in sample:
                print(f"  lid={r['lid'][:30]} -> phone_jid={r['phone_jid'][:30]}")

        # Check group messages via the same join group_graph.py uses
        group_count = await conn.fetchval("SELECT COUNT(*) FROM whatsapp_chats WHERE is_group = true")
        group_msg_count = await conn.fetchval("""
            SELECT COUNT(*) FROM whatsapp_messages m
            JOIN whatsapp_chats c ON m.chat_id = c.id
            WHERE c.is_group = true AND m.sender_id IS NOT NULL
        """)
        lid_senders = await conn.fetchval("""
            SELECT COUNT(DISTINCT u.platform_user_id) FROM whatsapp_messages m
            JOIN whatsapp_chats c ON m.chat_id = c.id
            JOIN whatsapp_users u ON m.sender_id = u.id
            WHERE c.is_group = true AND u.platform_user_id LIKE '%@lid'
        """)
        phone_senders = await conn.fetchval("""
            SELECT COUNT(DISTINCT u.platform_user_id) FROM whatsapp_messages m
            JOIN whatsapp_chats c ON m.chat_id = c.id
            JOIN whatsapp_users u ON m.sender_id = u.id
            WHERE c.is_group = true AND u.platform_user_id LIKE '%@s.whatsapp.net'
        """)
        print(f"\nGroup chats: {group_count}, group messages: {group_msg_count}")
        print(f"Unique senders — @lid: {lid_senders}, @s.whatsapp.net: {phone_senders}")

    async with analyzer.acquire() as conn:
        rel_count = await conn.fetchval("SELECT COUNT(*) FROM entity_relationships WHERE relationship_type = 'whatsapp_cogroup'")
        print(f"\nentity_relationships (whatsapp_cogroup): {rel_count}")
        if rel_count > 0:
            sample_rels = await conn.fetch("""
                SELECT entity_a_id::text, entity_b_id::text, weight
                FROM entity_relationships WHERE relationship_type = 'whatsapp_cogroup'
                ORDER BY weight DESC LIMIT 5
            """)
            for r in sample_rels:
                print(f"  {r['entity_a_id'][:8]} <-> {r['entity_b_id'][:8]} weight={r['weight']}")

    await close_pools()

asyncio.run(main())
