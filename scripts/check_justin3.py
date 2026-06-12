import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_collector_pool

JUSTIN_PID = "6598526235@s.whatsapp.net"
KOKONUT_PID = "6585337719@s.whatsapp.net"

async def main():
    await init_pools()
    collector = get_collector_pool()

    async with collector.acquire() as conn:
        # How many LID entries map to Justin / Kokonut phone JIDs?
        for pid, name in [(JUSTIN_PID, "Justin"), (KOKONUT_PID, "Kokonuttree")]:
            lid_cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM whatsapp_lid_map WHERE phone_jid = $1", pid
            )
            print(f"{name} ({pid}) lid_map entries: {lid_cnt}")

        # Total messages per user (no from_me filter) via platform_user_id lookup
        for pid, name in [(JUSTIN_PID, "Justin"), (KOKONUT_PID, "Kokonuttree")]:
            # Direct messages via platform_user_id
            cnt_direct = await conn.fetchval("""
                SELECT COUNT(*) FROM whatsapp_messages m
                JOIN whatsapp_users u ON m.sender_id = u.id
                WHERE u.platform_user_id = $1 AND m.text IS NOT NULL
            """, pid)
            # Messages via LID map (different sender JIDs that resolve to same phone)
            cnt_lid = await conn.fetchval("""
                SELECT COUNT(*) FROM whatsapp_messages m
                JOIN whatsapp_users u ON m.sender_id = u.id
                JOIN whatsapp_lid_map lm ON lm.lid = u.platform_user_id
                WHERE lm.phone_jid = $1 AND m.text IS NOT NULL
            """, pid)
            print(f"\n{name}: direct_msgs={cnt_direct}  via_lid_msgs={cnt_lid}")
            if cnt_lid > 0:
                rows = await conn.fetch("""
                    SELECT m.text, m.timestamp FROM whatsapp_messages m
                    JOIN whatsapp_users u ON m.sender_id = u.id
                    JOIN whatsapp_lid_map lm ON lm.lid = u.platform_user_id
                    WHERE lm.phone_jid = $1 AND m.text IS NOT NULL
                    ORDER BY m.timestamp DESC LIMIT 3
                """, pid)
                for r in rows:
                    print(f"  [{r['timestamp']}] {str(r['text'])[:100]!r}")

        # Check whatsapp_chats table columns
        cols = await conn.fetch("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'whatsapp_chats'
            ORDER BY ordinal_position
        """)
        print(f"\nwhatsapp_chats columns: {[c['column_name'] for c in cols]}")

    await close_pools()

asyncio.run(main())
