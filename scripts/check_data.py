import asyncio
from src.db.connection import init_pools, close_pools, get_collector_pool, get_analyzer_pool


async def main():
    await init_pools()
    c = get_collector_pool()
    a = get_analyzer_pool()

    async with c.acquire() as conn:
        lid_count = await conn.fetchval("SELECT COUNT(*) FROM whatsapp_lid_map")
        print(f"LID map entries: {lid_count}")
        sample = await conn.fetch(
            "SELECT lid, phone_jid, display_name FROM whatsapp_lid_map LIMIT 5"
        )
        for r in sample:
            print(f"  {r['lid'][:30]} -> {r['phone_jid']} name={r['display_name']}")

        # How many LID group senders can now be resolved?
        resolvable = await conn.fetchval("""
            SELECT COUNT(DISTINCT u.platform_user_id)
            FROM whatsapp_messages m
            JOIN whatsapp_chats ch ON m.chat_id = ch.id
            JOIN whatsapp_users u ON m.sender_id = u.id
            JOIN whatsapp_lid_map lm ON u.platform_user_id = lm.lid
            WHERE ch.is_group = true AND m.sender_id IS NOT NULL
        """)
        total_lid_senders = await conn.fetchval("""
            SELECT COUNT(DISTINCT u.platform_user_id)
            FROM whatsapp_messages m
            JOIN whatsapp_chats ch ON m.chat_id = ch.id
            JOIN whatsapp_users u ON m.sender_id = u.id
            WHERE ch.is_group = true AND u.platform_user_id LIKE '%@lid%'
        """)
        print(f"LID group senders: {total_lid_senders} total, {resolvable} resolvable via lid_map")

        # Strava
        strava_count = await conn.fetchval("SELECT COUNT(*) FROM strava_athletes")
        strava_with_user = await conn.fetchval(
            "SELECT COUNT(*) FROM strava_athletes WHERE username IS NOT NULL AND username != ''"
        )
        print(f"\nStrava athletes: {strava_count} total, {strava_with_user} with username")
        sample_s = await conn.fetch(
            "SELECT platform_athlete_id, username, firstname, lastname "
            "FROM strava_athletes WHERE username IS NOT NULL LIMIT 5"
        )
        for r in sample_s:
            print(f"  id={r['platform_athlete_id']} user={r['username']} "
                  f"name={r['firstname']} {r['lastname']}")

    async with a.acquire() as conn:
        strava_links = await conn.fetchval(
            "SELECT COUNT(*) FROM entity_platform_links WHERE source = 'strava'"
        )
        print(f"Strava entity_platform_links: {strava_links}")

        # Check what strava platform_ids look like
        strava_pids = await conn.fetch(
            "SELECT platform_id, platform_username FROM entity_platform_links "
            "WHERE source = 'strava' LIMIT 5"
        )
        for r in strava_pids:
            print(f"  platform_id={r['platform_id']} username={r['platform_username']}")

    await close_pools()


asyncio.run(main())
