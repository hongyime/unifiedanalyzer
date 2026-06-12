import asyncio, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool
from src.pipeline.graph_analytics import compute_graph_analytics


async def main():
    await init_pools()

    stats = await compute_graph_analytics()
    print(f"Stats: {stats}")
    print(f"communities_found: {stats.get('communities_found')}")
    print(f"largest_community_size: {stats.get('largest_community_size')}")

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT bp.metadata->>'community_id' AS community_id,
                   e.id::text AS entity_id, e.canonical_name
            FROM behavioral_profiles bp
            JOIN entities e ON e.id = bp.entity_id
            WHERE bp.metadata->>'community_id' IS NOT NULL
        """)

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["community_id"], []).append({
            "entity_id": r["entity_id"],
            "canonical_name": r["canonical_name"],
        })

    communities = sorted(
        ({"community_id": cid, "member_count": len(m), "members": m} for cid, m in grouped.items() if len(m) >= 2),
        key=lambda c: c["member_count"],
        reverse=True,
    )

    print(f"\nTotal communities (>=2 members): {len(communities)}")
    for c in communities[:5]:
        names = [m["canonical_name"] for m in c["members"]]
        print(f"  community_id={c['community_id']} size={c['member_count']} members={names}")

    await close_pools()


asyncio.run(main())
