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
        for pid, name in [(JUSTIN_PID, "Justin"), (KOKONUT_PID, "Kokonuttree")]:
            cnt = await conn.fetchval("""
                SELECT COUNT(*) FROM whatsapp_messages m
                JOIN whatsapp_users u ON m.sender_id = u.id
                WHERE u.platform_user_id = $1 AND m.from_me = false AND m.text IS NOT NULL
            """, pid)
            rows = await conn.fetch("""
                SELECT m.text, m.timestamp FROM whatsapp_messages m
                JOIN whatsapp_users u ON m.sender_id = u.id
                WHERE u.platform_user_id = $1 AND m.from_me = false AND m.text IS NOT NULL
                ORDER BY m.timestamp DESC
                LIMIT 5
            """, pid)
            print(f"\n{name} ({pid}) — {cnt} messages:")
            for r in rows:
                print(f"  [{r['timestamp']}] {str(r['text'])[:120]!r}")

        # Check shared groups
        shared = await conn.fetch("""
            SELECT DISTINCT m1.chat_id::text, c.chat_name
            FROM whatsapp_messages m1
            JOIN whatsapp_users u1 ON m1.sender_id = u1.id
            JOIN whatsapp_messages m2 ON m2.chat_id = m1.chat_id
            JOIN whatsapp_users u2 ON m2.sender_id = u2.id
            LEFT JOIN whatsapp_chats c ON c.chat_id = m1.chat_id
            WHERE u1.platform_user_id = $1 AND u2.platform_user_id = $2
        """, JUSTIN_PID, KOKONUT_PID)
        print(f"\nShared groups between Justin and Kokonuttree: {len(shared)}")
        for g in shared:
            print(f"  {g['chat_id'][:50]!r} name={g['chat_name']!r}")

    await close_pools()

asyncio.run(main())
