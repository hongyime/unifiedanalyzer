import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool
from src.pipeline.identity_scorer import compute_identity_scores


def _decode_meta(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


async def main():
    await init_pools()

    stats = await compute_identity_scores()
    print(f"Stats: {stats}")

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT er.weight, er.cross_platform, er.sources,
                   e1.canonical_name AS name_a, e2.canonical_name AS name_b
            FROM entity_relationships er
            JOIN entities e1 ON e1.id = er.entity_a_id
            JOIN entities e2 ON e2.id = er.entity_b_id
            WHERE er.relationship_type = 'same_person_probability'
            ORDER BY er.weight DESC
        """)
        print(f"\nsame_person_probability pairs (score >= 0.10): {len(rows)}")
        for r in rows:
            src = _decode_meta(r["sources"])
            score = src.get("score")
            breakdown = src.get("contributing_signals", [])
            print(f"  {r['name_a']!r} <-> {r['name_b']!r}  score={score} weight={r['weight']} cross_platform={r['cross_platform']}")
            for b in breakdown:
                print(f"      - {b['type']}: confidence={b['confidence']}")

    await close_pools()


asyncio.run(main())
