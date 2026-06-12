import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool, get_collector_pool

PIDS = {
    "Natasha Chang": "UCkJt_7wHOZhnKUZy49sL9aQ",
    "vi ☻ ": "UCqxZNt5DPbgjnhNkyZYsGaQ",
}


def _decode(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            r = json.loads(raw)
            return r if isinstance(r, dict) else {}
        except Exception:
            return {}
    return {}


async def main():
    await init_pools()
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    async with analyzer.acquire() as conn:
        for name in PIDS:
            row = await conn.fetchrow("""
                SELECT bp.metadata->'content_fingerprint' AS fp
                FROM behavioral_profiles bp
                JOIN entities e ON e.id = bp.entity_id
                WHERE e.canonical_name = $1
            """, name)
            fp = _decode(row["fp"]) if row else {}
            print(f"\n=== {name} content_fingerprint ===")
            print(f"  vocab_size={fp.get('vocab_size')} richness={fp.get('vocab_richness')} post_count={fp.get('post_count')} avg_words={fp.get('avg_words_per_post')}")
            print(f"  top_words: {fp.get('top_words')}")

    async with collector.acquire() as conn:
        for name, pid in PIDS.items():
            rows = await conn.fetch("""
                SELECT v.title, v.description
                FROM youtube_videos v
                JOIN youtube_channels ch ON v.channel_id = ch.id
                WHERE ch.platform_channel_id = $1 AND v.description IS NOT NULL AND length(v.description) > 20
                ORDER BY v.collected_at DESC
                LIMIT 5
            """, pid)
            print(f"\n=== {name} sample video descriptions ({len(rows)} shown) ===")
            for r in rows:
                desc = (r["description"] or "").replace("\n", " | ")
                print(f"  {r['title']!r}")
                print(f"     desc: {desc[:400]}")

    await close_pools()


asyncio.run(main())
