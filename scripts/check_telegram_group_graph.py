import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool
from src.pipeline.group_graph import build_telegram_group_graph


async def main():
    await init_pools()

    stats = await build_telegram_group_graph()
    print(f"Stats: {stats}")

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rels = await conn.fetch("""
            SELECT r.entity_a_id::text, a.canonical_name AS name_a,
                   r.entity_b_id::text, b.canonical_name AS name_b,
                   r.weight, r.sources
            FROM entity_relationships r
            JOIN entities a ON a.id = r.entity_a_id
            JOIN entities b ON b.id = r.entity_b_id
            WHERE r.relationship_type = 'telegram_group_co_member'
        """)
        print(f"\n=== telegram_group_co_member ({len(rels)}) ===")
        for r in rels:
            print(f"  {r['name_a']!r} <-> {r['name_b']!r} weight={r['weight']} sources={r['sources']}")

        sigs = await conn.fetch("""
            SELECT s.entity_id::text, e.canonical_name AS name_a,
                   s.target_record_id, t.canonical_name AS name_b,
                   s.value, s.confidence
            FROM identity_signals s
            JOIN entities e ON e.id = s.entity_id
            LEFT JOIN entities t ON t.id::text = s.target_record_id
            WHERE s.signal_type = 'group_cooccurrence' AND s.source_platform = 'telegram'
        """)
        print(f"\n=== group_cooccurrence (telegram) ({len(sigs)}) ===")
        for s in sigs:
            print(f"  {s['name_a']!r} <-> {s['name_b']!r} | {s['value']!r} conf={s['confidence']}")

        # sanity: make sure whatsapp signals are untouched
        wa_sigs = await conn.fetchval(
            "SELECT COUNT(*) FROM identity_signals WHERE signal_type = 'group_cooccurrence' AND source_platform = 'whatsapp'"
        )
        print(f"\ngroup_cooccurrence (whatsapp) still present: {wa_sigs}")

    await close_pools()


asyncio.run(main())
