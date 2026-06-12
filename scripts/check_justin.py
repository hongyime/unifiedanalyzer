import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool

async def main():
    await init_pools()
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.id::text, e.canonical_name, epl.source, epl.platform_id, epl.platform_username
            FROM entities e
            JOIN entity_platform_links epl ON epl.entity_id = e.id
            WHERE e.canonical_name IN ('Justin', 'Kokonuttree')
            ORDER BY e.canonical_name, epl.source
        """)
        print("Justin / Kokonuttree platform links:")
        for r in rows:
            print(f"  {r['canonical_name']!r}  src={r['source']}  pid={r['platform_id']}  uname={r['platform_username']}")

        rows = await conn.fetch("""
            SELECT e.canonical_name, bp.total_events, bp.metadata->'content_fingerprint' AS fp
            FROM behavioral_profiles bp
            JOIN entities e ON e.id = bp.entity_id
            WHERE e.canonical_name IN ('Justin', 'Kokonuttree')
        """)
        print()
        for r in rows:
            fp = r['fp']
            if isinstance(fp, str):
                fp = json.loads(fp)
            if fp:
                tw = fp.get('top_words', [])[:10]
                print(f"{r['canonical_name']!r}: events={r['total_events']} tokens={fp.get('token_count')} vocab={fp.get('vocab_size')} avg_words={fp.get('avg_words_per_post')}")
                print(f"  top_words: {tw}")

        cnt5 = await conn.fetchval("SELECT COUNT(*) FROM behavioral_profiles WHERE posting_hour_dist IS NOT NULL AND total_events >= 5")
        cnt2 = await conn.fetchval("SELECT COUNT(*) FROM behavioral_profiles WHERE posting_hour_dist IS NOT NULL AND total_events >= 2")
        print(f"\nProfiles eligible temporal corr (>=5 events): {cnt5}")
        print(f"Profiles eligible temporal corr (>=2 events): {cnt2}")

        # Find Justin and Kokonuttree entity IDs
        eids = await conn.fetch("""
            SELECT e.id::text, e.canonical_name
            FROM entities e
            WHERE e.canonical_name IN ('Justin', 'Kokonuttree')
        """)
        for r in eids:
            eid = r['id']
            name = r['canonical_name']
            # Get their platform links
            links = await conn.fetch("""
                SELECT source, platform_id, platform_username
                FROM entity_platform_links
                WHERE entity_id = $1::uuid
            """, eid)
            print(f"\n{name!r} (id={eid[:8]}...):")
            for l in links:
                print(f"  {l['source']}  pid={l['platform_id']}  uname={l['platform_username']}")

    # Check TikTok profiles for these usernames
    async with collector.acquire() as conn:
        tt = await conn.fetch("""
            SELECT unique_id, nickname, signature
            FROM tiktok_profiles
            WHERE unique_id ILIKE '%justin%' OR nickname ILIKE '%justin%'
               OR unique_id ILIKE '%kokonut%' OR nickname ILIKE '%kokonut%'
            LIMIT 10
        """)
        print(f"\nTikTok profiles matching Justin/Kokonuttree: {len(tt)}")
        for r in tt:
            print(f"  {r['unique_id']!r} nick={r['nickname']!r} sig={str(r['signature'])[:60]!r}")

        # Check YouTube channels
        yt = await conn.fetch("""
            SELECT platform_channel_id, channel_name, country, description
            FROM youtube_channels
            WHERE channel_name ILIKE '%justin%' OR channel_name ILIKE '%kokonut%'
            LIMIT 10
        """)
        print(f"\nYouTube channels matching: {len(yt)}")
        for r in yt:
            print(f"  {r['platform_channel_id']!r} name={r['channel_name']!r} country={r['country']!r}")

    await close_pools()

asyncio.run(main())
