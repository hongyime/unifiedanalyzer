import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool

NAMES = ["Natasha Chang", "vi ☻ "]


async def main():
    await init_pools()
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    async with analyzer.acquire() as conn:
        for name in NAMES:
            ent = await conn.fetchrow("SELECT id::text, canonical_name, tier FROM entities WHERE canonical_name = $1", name)
            print(f"\n=== {name!r} -> entity {ent['id'] if ent else None} (tier={ent['tier'] if ent else None}) ===")
            if not ent:
                continue
            links = await conn.fetch(
                "SELECT source, platform_id, platform_username, confidence FROM entity_platform_links WHERE entity_id = $1::uuid",
                ent["id"],
            )
            for l in links:
                print(f"  link: {l['source']} pid={l['platform_id']!r} username={l['platform_username']!r} conf={l['confidence']}")

            sigs = await conn.fetch(
                "SELECT signal_type, target_platform, target_record_id, value, confidence FROM identity_signals WHERE entity_id = $1::uuid",
                ent["id"],
            )
            for s in sigs:
                print(f"  signal: {dict(s)}")

    # Now pull raw youtube channel data for each entity's youtube platform_id
    async with analyzer.acquire() as conn:
        pid_map = {}
        for name in NAMES:
            row = await conn.fetchrow("""
                SELECT epl.platform_id, epl.platform_username
                FROM entities e
                JOIN entity_platform_links epl ON epl.entity_id = e.id
                WHERE e.canonical_name = $1 AND epl.source = 'youtube'
            """, name)
            if row:
                pid_map[name] = (row["platform_id"], row["platform_username"])

    print(f"\npid_map: {pid_map}")

    async with collector.acquire() as conn:
        cols = await conn.fetch("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'youtube_channels' ORDER BY ordinal_position
        """)
        print(f"\nyoutube_channels columns: {[c['column_name'] for c in cols]}")

        for name, (pid, username) in pid_map.items():
            row = await conn.fetchrow(
                "SELECT * FROM youtube_channels WHERE platform_channel_id = $1", pid
            )
            print(f"\n--- {name} youtube channel (pid={pid}) ---")
            if row:
                for k, v in dict(row).items():
                    sval = str(v)
                    print(f"  {k}: {sval[:300]}")
            else:
                print("  (no channel row found)")

    await close_pools()


asyncio.run(main())
