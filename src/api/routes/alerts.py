from datetime import datetime, timezone
import json
from fastapi import APIRouter, Query, HTTPException

from src.db.connection import get_analyzer_pool
from src.pipeline.incremental_runner import run_incremental

router = APIRouter(tags=["alerts"])


def _decode_jsonb(raw, default=None):
    if raw is None:
        return [] if default is None else default
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
    return raw


@router.get("/alerts")
async def list_alerts(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    unread_only: bool = False,
    entity_id: str | None = None,
):
    pool = get_analyzer_pool()
    offset = (page - 1) * per_page

    conditions: list[str] = []
    params: list = []
    idx = 1

    if unread_only:
        conditions.append("a.is_read = FALSE")

    if entity_id:
        conditions.append(f"a.entity_id = ${idx}::uuid")
        params.append(entity_id)
        idx += 1

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM alerts a {where}", *params
        )

        params.extend([per_page, offset])
        rows = await conn.fetch(f"""
            SELECT a.id, a.entity_id, a.alert_type, a.severity, a.source,
                   a.title, a.detail, a.detected_at, a.is_read, a.read_at,
                   e.canonical_name
            FROM alerts a
            LEFT JOIN entities e ON a.entity_id = e.id
            {where}
            ORDER BY a.detected_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """, *params)

    return {
        "data": [
            {
                "id": str(r["id"]),
                "entity_id": str(r["entity_id"]) if r["entity_id"] else None,
                "entity_name": r["canonical_name"],
                "alert_type": r["alert_type"],
                "severity": r["severity"],
                "source": r["source"],
                "title": r["title"],
                "detail": r["detail"],
                "detected_at": r["detected_at"].isoformat(),
                "is_read": r["is_read"],
                "read_at": r["read_at"].isoformat() if r["read_at"] else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.post("/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE alerts SET is_read = TRUE, read_at = NOW()
            WHERE id = $1::uuid AND is_read = FALSE
        """, alert_id)
    if result == "UPDATE 0":
        raise HTTPException(404, "Alert not found or already read")
    return {"ok": True}


@router.post("/alerts/read-all")
async def mark_all_read():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE alerts SET is_read = TRUE, read_at = NOW()
            WHERE is_read = FALSE
        """)
    return {"ok": True}


@router.get("/runs")
async def list_runs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    pool = get_analyzer_pool()
    offset = (page - 1) * per_page

    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM analysis_runs")
        rows = await conn.fetch("""
            SELECT id, run_type, status, started_at, finished_at,
                   entities_processed, events_created, alerts_created,
                   signals_created, error_message
            FROM analysis_runs
            ORDER BY started_at DESC
            LIMIT $1 OFFSET $2
        """, per_page, offset)

    return {
        "data": [
            {
                "id": str(r["id"]),
                "run_type": r["run_type"],
                "status": r["status"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                "entities_processed": r["entities_processed"],
                "events_created": r["events_created"],
                "alerts_created": r["alerts_created"],
                "signals_created": r["signals_created"],
                "error_message": r["error_message"],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/runs/{run_id}/coverage")
async def get_run_coverage(run_id: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT phase, source, phase_status,
                   processed_count, attributed_count, unresolved_count,
                   skipped_count, error_count, top_unresolved_json,
                   duration_ms, resource_class, created_at
            FROM pipeline_coverage_snapshots
            WHERE run_id = $1::uuid
            ORDER BY created_at, phase, source
        """, run_id)

    return {
        "run_id": run_id,
        "data": [
            {
                "phase": r["phase"],
                "source": r["source"],
                "status": r["phase_status"],
                "processed_count": r["processed_count"],
                "attributed_count": r["attributed_count"],
                "unresolved_count": r["unresolved_count"],
                "skipped_count": r["skipped_count"],
                "error_count": r["error_count"],
                "top_unresolved": _decode_jsonb(r["top_unresolved_json"]),
                "duration_ms": r["duration_ms"],
                "resource_class": r["resource_class"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.post("/runs/trigger")
async def trigger_run():
    try:
        stats = await run_incremental()
        return {"ok": True, "stats": stats}
    except Exception as e:
        raise HTTPException(500, f"Run failed: {e}")
