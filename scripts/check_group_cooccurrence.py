import asyncio, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool
from src.pipeline.group_graph import build_whatsapp_group_graph


async def main():
    await init_pools()

    stats = await build_whatsapp_group_graph()
    print(f"Stats: {stats}")

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT er.weight, e1.canonical_name AS name_a, e2.canonical_name AS name_b
            FROM entity_relationships er
            JOIN entities e1 ON e1.id = er.entity_a_id
            JOIN entities e2 ON e2.id = er.entity_b_id
            WHERE er.relationship_type = 'whatsapp_group_co_member'
            ORDER BY er.weight DESC
        """)
        print("\nwhatsapp_group_co_member relationships (weight = shared group count):")
        for r in rows:
            print(f"  {r['name_a']!r} <-> {r['name_b']!r}  weight={r['weight']}")

        sig_rows = await conn.fetch("""
            SELECT sig.entity_id::text, sig.target_record_id, sig.value, sig.confidence,
                   e1.canonical_name AS src_name, e2.canonical_name AS tgt_name
            FROM identity_signals sig
            JOIN entities e1 ON e1.id = sig.entity_id
            LEFT JOIN entities e2 ON e2.id::text = sig.target_record_id
            WHERE sig.signal_type = 'group_cooccurrence'
            ORDER BY sig.confidence DESC
            LIMIT 10
        """)
        print(f"\ngroup_cooccurrence signals: {len(sig_rows)}")
        for r in sig_rows:
            print(f"  {r['src_name']!r} <-> {r['tgt_name']!r}  {r['value']} conf={r['confidence']}")

    await close_pools()


asyncio.run(main())
