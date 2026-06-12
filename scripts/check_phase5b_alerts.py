import asyncio, os, sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.chdir(r"C:\unifiedanalyzer")
from dotenv import load_dotenv; load_dotenv(r"C:\unifiedanalyzer\.env")
from src.db.connection import init_pools, close_pools, get_analyzer_pool
from src.pipeline.alert_engine import run_alerts


def _decode(raw):
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

    stats = await run_alerts()
    print(f"Stats: {stats}")

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        for alert_type in ("NEW_IDENTITY_LINK", "COORDINATED_POSTING", "LOCATION_MISMATCH"):
            rows = await conn.fetch("""
                SELECT a.id::text, a.entity_id::text, a.severity, a.title, a.detail, a.detected_at,
                       e.canonical_name
                FROM alerts a
                LEFT JOIN entities e ON e.id = a.entity_id
                WHERE a.alert_type = $1
                ORDER BY a.detected_at DESC
            """, alert_type)
            print(f"\n=== {alert_type} ({len(rows)} total) ===")
            for r in rows:
                detail = _decode(r["detail"])
                print(f"  [{r['severity']}] {r['title']}")
                print(f"      entity={r['canonical_name']!r} detected_at={r['detected_at']}")
                print(f"      detail={json.dumps(detail, default=str)}")
            if not rows:
                print("  (none)")

    await close_pools()


asyncio.run(main())
