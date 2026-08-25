import os
from datetime import datetime, timezone

from fastapi import APIRouter

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.face_bridge_audit import audit_face_bridge_collisions
from src.pipeline.indicator_export import supabase_export_config

router = APIRouter(tags=["health"])


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _age_seconds(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds()))


async def _run_freshness(conn, run_type: str, *, completed_stale_after_seconds: int, heartbeat_stale_after_seconds: int) -> dict:
    completed = await conn.fetchrow(
        """
        SELECT finished_at
        FROM analysis_runs
        WHERE status = 'completed' AND run_type = $1
        ORDER BY finished_at DESC
        LIMIT 1
        """,
        run_type,
    )
    running = await conn.fetchrow(
        """
        SELECT started_at, heartbeat_at, error_message
        FROM analysis_runs
        WHERE status = 'running' AND run_type = $1
        ORDER BY COALESCE(heartbeat_at, started_at) DESC
        LIMIT 1
        """,
        run_type,
    )
    completed_at = completed["finished_at"] if completed and completed["finished_at"] else None
    running_started_at = running["started_at"] if running and running["started_at"] else None
    running_heartbeat_at = running["heartbeat_at"] if running and running["heartbeat_at"] else None
    completed_age = _age_seconds(completed_at)
    heartbeat_age = _age_seconds(running_heartbeat_at or running_started_at)
    running_fresh = heartbeat_age is not None and heartbeat_age <= heartbeat_stale_after_seconds
    completed_fresh = completed_age is not None and completed_age <= completed_stale_after_seconds

    if running_fresh:
        state = "running"
        ok = True
        detail = "running run heartbeat is fresh"
    elif completed_fresh:
        state = "fresh"
        ok = True
        detail = "latest completed run is fresh"
    else:
        state = "stale"
        ok = False
        if completed_age is None:
            detail = "no completed run and no fresh running heartbeat"
        else:
            detail = f"latest completed run is {completed_age}s old (> {completed_stale_after_seconds}s)"

    return {
        "ok": ok,
        "state": state,
        "detail": detail,
        "last_completed_at": _iso(completed_at),
        "last_completed_age_seconds": completed_age,
        "completed_stale_after_seconds": completed_stale_after_seconds,
        "running_started_at": _iso(running_started_at),
        "running_heartbeat_at": _iso(running_heartbeat_at),
        "running_heartbeat_age_seconds": heartbeat_age,
        "running_heartbeat_stale_after_seconds": heartbeat_stale_after_seconds,
        "running_error": running["error_message"] if running and running["error_message"] else None,
    }


async def _supabase_export_health(conn) -> dict:
    config = supabase_export_config()
    mode = str(config.get("mode") or "disabled")
    threshold = _env_int("ANALYZER_HEALTH_SUPABASE_READY_WARN_THRESHOLD", 0, minimum=0)
    section = {
        "configured": bool(config.get("configured")),
        "mode": mode,
        "payload": config.get("payload"),
        "raw_mirror": False,
        "ok": True,
        "state": "disabled" if mode == "disabled" else "unknown",
        "ready_to_export": None,
        "exported_count": None,
        "pending_non_exportable": None,
        "ready_warn_threshold": threshold,
        "detail": "Supabase export disabled",
    }
    if mode == "disabled":
        return section
    try:
        exists = await conn.fetchval("SELECT to_regclass('public.normalized_indicators') IS NOT NULL")
        if not exists:
            section.update({
                "ok": False,
                "state": "schema_missing",
                "ready_to_export": 0,
                "exported_count": 0,
                "pending_non_exportable": 0,
                "detail": "normalized_indicators table is missing",
            })
            return section
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE supabase_exportable AND export_status IN ('pending','retry'))::int
                    AS ready_to_export,
                COUNT(*) FILTER (WHERE export_status = 'exported')::int AS exported_count,
                COUNT(*) FILTER (WHERE NOT supabase_exportable AND export_status = 'pending')::int
                    AS pending_non_exportable
            FROM normalized_indicators
            """
        )
        ready = int(row["ready_to_export"] or 0)
        exported = int(row["exported_count"] or 0)
        pending_non_exportable = int(row["pending_non_exportable"] or 0)
        section.update({
            "ready_to_export": ready,
            "exported_count": exported,
            "pending_non_exportable": pending_non_exportable,
        })
        if ready > threshold:
            section.update({
                "ok": False,
                "state": "backlog",
                "detail": f"{ready} exportable indicator(s) pending/retry (> {threshold})",
            })
        elif exported <= 0:
            section.update({
                "ok": False,
                "state": "not_populated",
                "detail": "no normalized indicators have been exported yet",
            })
        else:
            section.update({
                "ok": True,
                "state": "ok",
                "detail": "Supabase compact indicator export is drained and populated locally",
            })
        return section
    except Exception as exc:  # noqa: BLE001 - health should report degraded state
        section.update({
            "ok": False,
            "state": "error",
            "detail": f"{exc.__class__.__name__}: {exc}",
        })
        return section


async def _face_processing_health(conn) -> dict:
    threshold_seconds = _env_int("ANALYZER_HEALTH_FACE_PROCESSING_STALE_HOURS", 24, minimum=1) * 3600
    section = {
        "available": False,
        "ok": False,
        "state": "unknown",
        "image_count": 0,
        "face_count": 0,
        "entity_face_count": 0,
        "latest_image_at": None,
        "latest_image_age_seconds": None,
        "stale_after_seconds": threshold_seconds,
        "detail": "face processing status unavailable",
    }
    try:
        tables = await conn.fetchrow(
            """
            SELECT
                to_regclass('facetracker.images') IS NOT NULL AS images_exists,
                to_regclass('facetracker.faces') IS NOT NULL AS faces_exists,
                to_regclass('public.entity_faces') IS NOT NULL AS entity_faces_exists
            """
        )
        if not tables or not tables["images_exists"] or not tables["faces_exists"]:
            section.update({
                "available": False,
                "state": "schema_missing",
                "detail": "facetracker image/face tables are missing",
            })
            return section
        row = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*)::int FROM facetracker.images) AS image_count,
                (SELECT MAX(created_at) FROM facetracker.images) AS latest_image_at,
                (SELECT COUNT(*)::int FROM facetracker.faces) AS face_count,
                CASE WHEN $1::bool
                    THEN (SELECT COUNT(*)::int FROM public.entity_faces)
                    ELSE 0
                END AS entity_face_count
            """,
            bool(tables["entity_faces_exists"]),
        )
        latest = row["latest_image_at"] if row else None
        age = _age_seconds(latest)
        image_count = int(row["image_count"] or 0) if row else 0
        face_count = int(row["face_count"] or 0) if row else 0
        entity_face_count = int(row["entity_face_count"] or 0) if row else 0
        section.update({
            "available": True,
            "image_count": image_count,
            "face_count": face_count,
            "entity_face_count": entity_face_count,
            "latest_image_at": _iso(latest),
            "latest_image_age_seconds": age,
        })
        if image_count <= 0:
            section.update({
                "ok": False,
                "state": "empty",
                "detail": "no facetracker images have been indexed",
            })
        elif age is not None and age > threshold_seconds:
            section.update({
                "ok": False,
                "state": "stale",
                "detail": f"latest indexed face image is {age}s old (> {threshold_seconds}s)",
            })
        else:
            section.update({
                "ok": True,
                "state": "ok",
                "detail": "face processing has indexed images recently enough",
            })
        return section
    except Exception as exc:  # noqa: BLE001 - health should report degraded state
        section.update({
            "available": False,
            "ok": False,
            "state": "error",
            "detail": f"{exc.__class__.__name__}: {exc}",
        })
        return section


@router.get("/health")
async def health_check():
    status = {
        "status": "ok",
        "analyzer_db": "unknown",
        "collector_db": "unknown",
        "last_incremental_run": None,
        "last_full_resolution": None,
        "scheduler_freshness": {
            "incremental": None,
            "full_resolution": None,
        },
        "supabase_export": None,
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
        "face_processing": None,
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

            incremental_interval_seconds = _env_int("INCREMENTAL_RUN_INTERVAL_MINUTES", 60, minimum=1) * 60
            full_interval_seconds = _env_int("FULL_RESOLUTION_INTERVAL_HOURS", 12, minimum=1) * 3600
            heartbeat_stale_seconds = _env_int(
                "ANALYZER_HEALTH_RUNNING_RUN_HEARTBEAT_STALE_MINUTES",
                _env_int("STALE_RUN_HEARTBEAT_MINUTES", 90, minimum=1),
                minimum=1,
            ) * 60
            incremental_stale_seconds = _env_int(
                "ANALYZER_HEALTH_INCREMENTAL_STALE_MINUTES",
                max(180, (_env_int("INCREMENTAL_RUN_INTERVAL_MINUTES", 60, minimum=1) * 3)),
                minimum=1,
            ) * 60
            full_stale_seconds = _env_int(
                "ANALYZER_HEALTH_FULL_RESOLUTION_STALE_HOURS",
                max(24, _env_int("FULL_RESOLUTION_INTERVAL_HOURS", 12, minimum=1) * 2),
                minimum=1,
            ) * 3600

            incremental_freshness = await _run_freshness(
                conn,
                "incremental",
                completed_stale_after_seconds=max(incremental_stale_seconds, incremental_interval_seconds),
                heartbeat_stale_after_seconds=heartbeat_stale_seconds,
            )
            full_freshness = await _run_freshness(
                conn,
                "full_resolution",
                completed_stale_after_seconds=max(full_stale_seconds, full_interval_seconds),
                heartbeat_stale_after_seconds=heartbeat_stale_seconds,
            )
            status["scheduler_freshness"] = {
                "incremental": incremental_freshness,
                "full_resolution": full_freshness,
            }
            if not incremental_freshness["ok"] or not full_freshness["ok"]:
                status["status"] = "degraded"

            supabase_export = await _supabase_export_health(conn)
            status["supabase_export"] = supabase_export
            if supabase_export.get("ok") is not True:
                status["status"] = "degraded"

            try:
                backup = await conn.fetchrow("""
                    SELECT status, kinds, started_at, finished_at, path, size_bytes,
                           deleted_count, restore_validation, error_message
                    FROM analyzer_backup_runs
                    WHERE status = 'failed'
                       OR (status = 'success' AND path IS NOT NULL)
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

            face_processing = await _face_processing_health(conn)
            status["face_processing"] = face_processing
            if face_processing.get("ok") is not True:
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
