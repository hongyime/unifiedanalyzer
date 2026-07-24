from fastapi import APIRouter

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.face_bridge_audit import audit_face_bridge_collisions

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    status = {
        "status": "ok",
        "analyzer_db": "unknown",
        "collector_db": "unknown",
        "last_incremental_run": None,
        "last_full_resolution": None,
        "last_backup_run": None,
        "decision_log": {
            "pending_jsonl": None,
            "jsonl_errors": None,
            "latest_jsonl_written_at": None,
            "latest_jsonl_error_at": None,
            "latest_jsonl_error": None,
        },
        "face_bridge_audit": {
            "available": None,
            "ok": None,
            "face_entity_collisions": None,
            "cluster_entity_collisions": None,
            "contested_cluster_count": None,
            "samples": {"faces": [], "clusters": [], "contested_clusters": []},
        },
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

            try:
                backup = await conn.fetchrow("""
                    SELECT status, kinds, started_at, finished_at, path, size_bytes,
                           deleted_count, restore_validation, error_message
                    FROM analyzer_backup_runs
                    WHERE status = 'failed' OR path IS NOT NULL
                    ORDER BY started_at DESC
                    LIMIT 1
                """)
            except Exception:
                backup = None
            if backup:
                status["last_backup_run"] = {
                    "status": backup["status"],
                    "kinds": list(backup["kinds"] or []),
                    "started_at": backup["started_at"].isoformat()
                    if backup["started_at"] else None,
                    "finished_at": backup["finished_at"].isoformat()
                    if backup["finished_at"] else None,
                    "path": backup["path"],
                    "size_bytes": backup["size_bytes"],
                    "deleted_count": backup["deleted_count"],
                    "restore_validation": backup["restore_validation"],
                    "error_message": backup["error_message"],
                }

            try:
                decision_log = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE decision_jsonl_written_at IS NULL)::int AS pending_jsonl,
                        COUNT(*) FILTER (WHERE decision_jsonl_error IS NOT NULL)::int AS jsonl_errors,
                        MAX(decision_jsonl_written_at) AS latest_jsonl_written_at,
                        MAX(created_at) FILTER (WHERE decision_jsonl_error IS NOT NULL) AS latest_jsonl_error_at
                    FROM audit_log
                """)
                latest_error = await conn.fetchrow("""
                    SELECT decision_jsonl_error
                    FROM audit_log
                    WHERE decision_jsonl_error IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                """)
            except Exception:
                decision_log = None
                latest_error = None
            if decision_log:
                pending = int(decision_log["pending_jsonl"] or 0)
                errors = int(decision_log["jsonl_errors"] or 0)
                status["decision_log"] = {
                    "pending_jsonl": pending,
                    "jsonl_errors": errors,
                    "latest_jsonl_written_at": decision_log["latest_jsonl_written_at"].isoformat()
                    if decision_log["latest_jsonl_written_at"] else None,
                    "latest_jsonl_error_at": decision_log["latest_jsonl_error_at"].isoformat()
                    if decision_log["latest_jsonl_error_at"] else None,
                    "latest_jsonl_error": latest_error["decision_jsonl_error"] if latest_error else None,
                }
                if pending or errors:
                    status["status"] = "degraded"

            face_audit = await audit_face_bridge_collisions(conn, sample_limit=5)
            status["face_bridge_audit"] = face_audit
            if face_audit.get("ok") is not True:
                status["status"] = "degraded"

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
