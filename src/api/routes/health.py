from fastapi import APIRouter

from src.db.connection import get_analyzer_pool, get_collector_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    status = {
        "status": "ok",
        "analyzer_db": "unknown",
        "collector_db": "unknown",
        "last_incremental_run": None,
        "last_full_resolution": None,
        "entity_count": 0,
        "alert_count_unread": 0,
    }

    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            status["analyzer_db"] = "connected"

            status["entity_count"] = await conn.fetchval(
                "SELECT COUNT(*) FROM entities"
            )
            status["alert_count_unread"] = await conn.fetchval(
                "SELECT COUNT(*) FROM alerts WHERE is_read = FALSE"
            )

            row = await conn.fetchrow("""
                SELECT run_type, finished_at FROM analysis_runs
                WHERE status = 'completed'
                  AND run_type IN ('incremental', 'full_resolution')
                ORDER BY finished_at DESC LIMIT 1
            """)
            if row:
                status[f"last_{row['run_type']}_run"] = row["finished_at"].isoformat()

            inc = await conn.fetchrow("""
                SELECT finished_at FROM analysis_runs
                WHERE status = 'completed' AND run_type = 'incremental'
                ORDER BY finished_at DESC LIMIT 1
            """)
            if inc and inc["finished_at"]:
                status["last_incremental_run"] = inc["finished_at"].isoformat()

            full = await conn.fetchrow("""
                SELECT finished_at FROM analysis_runs
                WHERE status = 'completed' AND run_type = 'full_resolution'
                ORDER BY finished_at DESC LIMIT 1
            """)
            if full and full["finished_at"]:
                status["last_full_resolution"] = full["finished_at"].isoformat()

    except Exception as e:
        status["analyzer_db"] = f"error: {e}"
        status["status"] = "degraded"

    try:
        pool = get_collector_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            status["collector_db"] = "connected"
    except Exception as e:
        status["collector_db"] = f"error: {e}"
        status["status"] = "degraded"

    return status
