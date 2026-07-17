"""Live health websocket for the dashboard.

GET (ws) /ws/health — pushes a compact health snapshot every few seconds so the
frontend status panel updates without polling. Mirrors the unifiedcollector
dashboard's /ws/health, adapted to analyzer state: overall status, entity/alert
counts, last run, per-source collector health, and media-analysis totals.

The route is registered on the app WITHOUT the /api prefix (so the path is
exactly /ws/health) and BEFORE the static-files mount, so it is matched ahead
of the catch-all frontend mount.
"""
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

router = APIRouter()

# How often to push a snapshot. Matches the collector dashboard cadence; cheap
# queries, single-user, so a few seconds is comfortable.
PUSH_INTERVAL_SECONDS = 5


async def build_health_snapshot() -> dict:
    """Compact live-health payload. Never raises — degrades fields on error."""
    snap: dict = {
        "status": "ok",
        "analyzer_db": "unknown",
        "collector_db": "unknown",
        "entity_count": 0,
        "alert_count_unread": 0,
        "last_completed_run": None,
        "media_items_analyzed": 0,
        "sources": [],
    }

    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            snap["analyzer_db"] = "connected"
            snap["entity_count"] = await conn.fetchval("SELECT COUNT(*) FROM entities")
            snap["alert_count_unread"] = await conn.fetchval(
                "SELECT COUNT(*) FROM alerts WHERE is_read = FALSE"
            )
            snap["media_items_analyzed"] = await conn.fetchval(
                "SELECT COUNT(DISTINCT media_item_id) FROM media_analysis"
            )
            last = await conn.fetchrow(
                "SELECT run_type, finished_at FROM analysis_runs "
                "WHERE status = 'completed' "
                "AND run_type IN ('incremental', 'full_resolution') "
                "ORDER BY finished_at DESC LIMIT 1"
            )
            if last and last["finished_at"]:
                snap["last_completed_run"] = {
                    "run_type": last["run_type"],
                    "finished_at": last["finished_at"].isoformat(),
                }
    except Exception as e:  # noqa: BLE001
        snap["analyzer_db"] = f"error: {e.__class__.__name__}"
        snap["status"] = "degraded"

    # Per-source collector health: green/amber/red derived from recent runs.
    try:
        pool = get_collector_pool()
        async with pool.acquire() as conn:
            snap["collector_db"] = "connected"
            rows = await conn.fetch(
                """
                SELECT source,
                       MAX(completed_at) AS last_completed,
                       SUM(items_failed) FILTER (WHERE completed_at > NOW() - INTERVAL '24 hours') AS failed_24h,
                       SUM(items_collected) FILTER (WHERE completed_at > NOW() - INTERVAL '24 hours') AS items_24h
                FROM collection_runs
                GROUP BY source
                ORDER BY source
                """
            )
        sources = []
        for r in rows:
            failed = r["failed_24h"] or 0
            items = r["items_24h"] or 0
            last_completed = r["last_completed"]
            # Health heuristic: red if nothing completed in 24h+, amber if
            # there were failures, otherwise green.
            if last_completed is None:
                health = "red"
            elif failed > 0 and items == 0:
                health = "red"
            elif failed > 0:
                health = "amber"
            else:
                health = "green"
            sources.append({
                "source": r["source"],
                "health": health,
                "last_completed": last_completed.isoformat() if last_completed else None,
                "items_24h": items,
                "failed_24h": failed,
            })
        snap["sources"] = sources
        if any(s["health"] == "red" for s in sources) and snap["status"] == "ok":
            snap["status"] = "degraded"
    except Exception as e:  # noqa: BLE001
        snap["collector_db"] = f"error: {e.__class__.__name__}"
        snap["status"] = "degraded"

    return snap


@router.websocket("/ws/health")
async def ws_health(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            snapshot = await build_health_snapshot()
            await websocket.send_json(snapshot)
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return
    except Exception as e:  # noqa: BLE001 — don't let one socket crash the worker
        logger.debug("ws/health closed: %s", e)
        try:
            await websocket.close()
        except Exception:
            pass
