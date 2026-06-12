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
        # Content similarity signals
        print("=== Content similarity signals ===")
        rows = await conn.fetch("""
            SELECT sig.entity_id::text, sig.target_record_id, sig.value,
                   e1.canonical_name AS src_name, e2.canonical_name AS tgt_name
            FROM identity_signals sig
            JOIN entities e1 ON e1.id = sig.entity_id
            LEFT JOIN entities e2 ON e2.id::text = sig.target_record_id
            WHERE sig.signal_type = 'content_similarity'
        """)
        for r in rows:
            print(f"  {r['src_name']!r} <-> {r['tgt_name']!r}  sim={r['value']}")

        # Temporal copost signals
        print("\n=== Temporal copost signals ===")
        rows = await conn.fetch("""
            SELECT sig.entity_id::text, sig.target_record_id, sig.value, sig.confidence,
                   e1.canonical_name AS src_name
            FROM identity_signals sig
            JOIN entities e1 ON e1.id = sig.entity_id
            WHERE sig.signal_type = 'temporal_copost'
            ORDER BY sig.confidence DESC LIMIT 10
        """)
        for r in rows:
            print(f"  {r['src_name']!r} copost={r['value']} conf={r['confidence']}")
        if not rows:
            print("  (none)")

        # Temporal hour similarity relationships
        print("\n=== Temporal hour similarity relationships ===")
        rows = await conn.fetch("""
            SELECT er.weight, er.sources,
                   e1.canonical_name AS name_a, e2.canonical_name AS name_b
            FROM entity_relationships er
            JOIN entities e1 ON e1.id = er.entity_a_id
            JOIN entities e2 ON e2.id = er.entity_b_id
            WHERE er.relationship_type = 'temporal_hour_similarity'
            ORDER BY er.weight DESC LIMIT 10
        """)
        for r in rows:
            src = r['sources']
            if isinstance(src, str):
                src = json.loads(src)
            print(f"  {r['name_a']!r} <-> {r['name_b']!r}  sim={src.get('similarity')}")
        if not rows:
            print("  (none)")

        # Location inference
        print("\n=== Location inference (sample) ===")
        rows = await conn.fetch("""
            SELECT e.canonical_name, bp.metadata->'location_inference' AS loc
            FROM behavioral_profiles bp
            JOIN entities e ON e.id = bp.entity_id
            WHERE bp.metadata ? 'location_inference'
            LIMIT 10
        """)
        for r in rows:
            loc = r['loc']
            if isinstance(loc, str): loc = json.loads(loc)
            print(f"  {r['canonical_name']!r}: country={loc.get('primary_country')} tz={loc.get('primary_timezone')} region={loc.get('region')}")
        if not rows:
            print("  (none yet)")

        # Content fingerprints
        print("\n=== Content fingerprints (sample) ===")
        rows = await conn.fetch("""
            SELECT e.canonical_name, bp.metadata->'content_fingerprint' AS fp
            FROM behavioral_profiles bp
            JOIN entities e ON e.id = bp.entity_id
            WHERE bp.metadata ? 'content_fingerprint'
            ORDER BY (bp.metadata->'content_fingerprint'->>'token_count')::int DESC NULLS LAST
            LIMIT 5
        """)
        for r in rows:
            fp = r['fp']
            if isinstance(fp, str): fp = json.loads(fp)
            print(f"  {r['canonical_name']!r}: tokens={fp.get('token_count')} vocab={fp.get('vocab_size')} avg_words={fp.get('avg_words_per_post')}")
        if not rows:
            print("  (none)")

        # Behavioral profiles with hour_dist (for temporal correlation)
        print("\n=== Behavioral profiles with posting_hour_dist ===")
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM behavioral_profiles WHERE posting_hour_dist IS NOT NULL AND total_events >= 10"
        )
        print(f"  Profiles eligible for temporal correlation: {cnt}")

    # Check YouTube countries
    print("\n=== YouTube channel country data ===")
    async with collector.acquire() as conn:
        rows = await conn.fetch(
            "SELECT platform_channel_id, country FROM youtube_channels WHERE country IS NOT NULL AND country != '' LIMIT 5"
        )
        print(f"  YouTube channels with country: {len(rows)}")
        for r in rows:
            print(f"    {r['platform_channel_id']} -> {r['country']}")

    await close_pools()

asyncio.run(main())
