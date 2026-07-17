import logging

from fastapi import APIRouter

from src.db.connection import get_collector_pool

router = APIRouter(tags=["collector-health"])
logger = logging.getLogger(__name__)


@router.get("/collector/health")
async def collector_health():
    try:
        pool = get_collector_pool()
        async with pool.acquire() as conn:
            runs = await conn.fetch("""
                SELECT source, status,
                       MAX(started_at) AS last_started,
                       MAX(completed_at) AS last_completed,
                       SUM(items_collected) FILTER (WHERE completed_at > NOW() - INTERVAL '24 hours') AS items_24h,
                       SUM(items_failed) FILTER (WHERE completed_at > NOW() - INTERVAL '24 hours') AS failed_24h,
                       COUNT(*) FILTER (WHERE completed_at > NOW() - INTERVAL '24 hours') AS runs_24h
                FROM collection_runs
                GROUP BY source, status
                ORDER BY source
            """)

            targets = await conn.fetch("""
                SELECT source, status, COUNT(*) AS count,
                       MAX(last_collection_at) AS last_collection
                FROM collection_targets
                GROUP BY source, status
                ORDER BY source
            """)
    except Exception as e:  # noqa: BLE001 - collector health is optional for analyzer uptime
        logger.warning("collector health skipped: %s", e)
        return {"collectors": [], "collector_db": "unreachable", "error": str(e)[:300]}

    collectors: dict = {}
    for r in runs:
        src = r["source"]
        if src not in collectors:
            collectors[src] = {
                "source": src,
                "last_started": None,
                "last_completed": None,
                "items_24h": 0,
                "failed_24h": 0,
                "runs_24h": 0,
                "latest_status": None,
                "targets": [],
            }
        c = collectors[src]
        if r["last_started"]:
            if not c["last_started"] or r["last_started"] > c["last_started"]:
                c["last_started"] = r["last_started"]
                c["latest_status"] = r["status"]
        if r["last_completed"] and (not c["last_completed"] or r["last_completed"] > c["last_completed"]):
            c["last_completed"] = r["last_completed"]
        c["items_24h"] += r["items_24h"] or 0
        c["failed_24h"] += r["failed_24h"] or 0
        c["runs_24h"] += r["runs_24h"] or 0

    for t in targets:
        src = t["source"]
        if src in collectors:
            collectors[src]["targets"].append({
                "status": t["status"],
                "count": t["count"],
                "last_collection": t["last_collection"].isoformat() if t["last_collection"] else None,
            })

    result = []
    for c in collectors.values():
        c["last_started"] = c["last_started"].isoformat() if c["last_started"] else None
        c["last_completed"] = c["last_completed"].isoformat() if c["last_completed"] else None
        result.append(c)

    return {"collectors": result}
