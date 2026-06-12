"""Verify join paths needed for Phase 4 pipeline queries."""
import asyncio, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_collector_pool

TABLES = ["tiktok_profiles", "lemon8_profiles", "telegram_users", "telegram_chats",
          "youtube_channels", "strava_athletes", "instagram_profiles"]

async def main():
    await init_pools()
    collector = get_collector_pool()

    async with collector.acquire() as conn:
        for table in TABLES:
            try:
                cols = await conn.fetch("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = $1 ORDER BY ordinal_position
                """, table)
                col_names = [r["column_name"] for r in cols]
                cnt = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                print(f"{table} ({cnt} rows): {col_names}")
            except Exception as e:
                print(f"{table}: {e}")

        # Verify join: tiktok_posts.profile_id → tiktok_profiles
        print("\n--- Join verification ---")
        try:
            r = await conn.fetchrow("""
                SELECT p.platform_user_id, COUNT(tp.id) AS post_count
                FROM tiktok_posts tp
                JOIN tiktok_profiles p ON tp.profile_id = p.id
                GROUP BY p.platform_user_id ORDER BY post_count DESC LIMIT 1
            """)
            print(f"tiktok_posts->tiktok_profiles: OK (sample: pid={r['platform_user_id']} posts={r['post_count']})")
        except Exception as e:
            print(f"tiktok join: {e}")

        try:
            r = await conn.fetchrow("""
                SELECT ch.platform_channel_id, COUNT(v.id) AS video_count
                FROM youtube_videos v
                JOIN youtube_channels ch ON v.channel_id = ch.id
                GROUP BY ch.platform_channel_id ORDER BY video_count DESC LIMIT 1
            """)
            print(f"youtube_videos->youtube_channels: OK (sample: pid={r['platform_channel_id']} vids={r['video_count']})")
        except Exception as e:
            print(f"youtube join: {e}")

        try:
            r = await conn.fetchrow("""
                SELECT u.platform_user_id, COUNT(m.id) AS msg_count
                FROM telegram_messages m
                JOIN telegram_users u ON m.sender_id = u.id
                GROUP BY u.platform_user_id ORDER BY msg_count DESC LIMIT 1
            """)
            print(f"telegram_messages->telegram_users: OK (sample: pid={r['platform_user_id']} msgs={r['msg_count']})")
        except Exception as e:
            print(f"telegram join: {e}")

        try:
            r = await conn.fetchrow("""
                SELECT u.platform_user_id, COUNT(m.id) AS msg_count
                FROM whatsapp_messages m
                JOIN whatsapp_users u ON m.sender_id = u.id
                GROUP BY u.platform_user_id ORDER BY msg_count DESC LIMIT 1
            """)
            print(f"whatsapp_messages->whatsapp_users: OK (sample: pid={r['platform_user_id'][:30]} msgs={r['msg_count']})")
        except Exception as e:
            print(f"whatsapp join: {e}")

        try:
            r = await conn.fetchrow("""
                SELECT p.platform_user_id, COUNT(lp.id) AS post_count
                FROM lemon8_posts lp
                JOIN lemon8_profiles p ON lp.profile_id = p.id
                GROUP BY p.platform_user_id ORDER BY post_count DESC LIMIT 1
            """)
            print(f"lemon8_posts->lemon8_profiles: OK (sample: pid={r['platform_user_id']} posts={r['post_count']})")
        except Exception as e:
            print(f"lemon8 join: {e}")

        # Strava: check timezone field format
        try:
            r = await conn.fetchrow("SELECT timezone, utc_offset, start_latlng FROM strava_activities WHERE timezone IS NOT NULL LIMIT 1")
            if r:
                print(f"strava timezone sample: {r['timezone']!r} utc_offset={r['utc_offset']}")
            else:
                print("strava timezone: still NULL (scraper hasn't run yet)")
        except Exception as e:
            print(f"strava timezone: {e}")

        # Lemon8 location
        try:
            r = await conn.fetchrow("SELECT location_name FROM lemon8_posts WHERE location_name IS NOT NULL AND location_name != '' LIMIT 1")
            print(f"lemon8 location_name sample: {r['location_name']!r}" if r else "lemon8 location_name: all null")
        except Exception as e:
            print(f"lemon8 location: {e}")

    await close_pools()

asyncio.run(main())
