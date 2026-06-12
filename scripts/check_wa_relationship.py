import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool

async def main():
    await init_pools()
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rels = await conn.fetch("""
            SELECT er.entity_a_id::text, er.entity_b_id::text, er.weight, er.sources,
                   e1.canonical_name AS name_a, e2.canonical_name AS name_b
            FROM entity_relationships er
            JOIN entities e1 ON e1.id = er.entity_a_id
            JOIN entities e2 ON e2.id = er.entity_b_id
            WHERE er.relationship_type = 'whatsapp_group_co_member'
        """)
        for r in rels:
            sources = r["sources"]
            if isinstance(sources, str):
                sources = json.loads(sources)
            print(f"entity_a: {r['entity_a_id'][:8]} name={r['name_a']!r}")
            print(f"entity_b: {r['entity_b_id'][:8]} name={r['name_b']!r}")
            print(f"  shared groups: {r['weight']}, groups={sources.get('groups', [])}")

            # Show platform links for both
            for eid, label in [(r['entity_a_id'], 'A'), (r['entity_b_id'], 'B')]:
                links = await conn.fetch(
                    "SELECT source, platform_id, platform_username, platform_name FROM entity_platform_links WHERE entity_id = $1::uuid",
                    eid
                )
                print(f"  Entity {label} platforms:")
                for l in links:
                    print(f"    {l['source']} id={l['platform_id']} user={l['platform_username']!r} name={l['platform_name']!r}")

    await close_pools()

asyncio.run(main())
