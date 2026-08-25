from datetime import datetime, timezone
import json
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from src.db.connection import get_analyzer_pool
from src.pipeline.stream_alerts import stream_alert_status

router = APIRouter(tags=["alerts"])


class AlertSuppressionIn(BaseModel):
    scope: str = "manual"
    alert_type: str | None = None
    entity_id: str | None = None
    source: str | None = None
    reason: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class AlertSuppressionPatch(BaseModel):
    reason: str | None = None
    ends_at: datetime | None = None
    status: str | None = None


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


@router.get("/alerts/stream/status")
async def get_stream_alert_status():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        return await stream_alert_status(conn)


@router.get("/alerts/fingerprints")
async def list_alert_fingerprints(
    alert_type: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    conditions: list[str] = []
    params: list = []
    if alert_type:
        params.append(alert_type)
        conditions.append(f"alert_type = ${len(params)}")
    if status:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    params.append(limit)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT fingerprint, alert_type, entity_id::text, source, window_start,
                   window_end, last_sent_at, count, status, detail, updated_at
            FROM alert_fingerprints
            {where}
            ORDER BY updated_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return {"data": [_stream_alert_row(row) for row in rows], "total": len(rows)}


@router.get("/alerts/windows")
async def list_alert_windows(
    bucket_type: str | None = None,
    source: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    conditions: list[str] = []
    params: list = []
    if bucket_type:
        params.append(bucket_type)
        conditions.append(f"alert_type = ${len(params)}")
    if source:
        params.append(source)
        conditions.append(f"source = ${len(params)}")
    params.append(limit)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT alert_type AS bucket_type,
                   bucket_key,
                   source,
                   bucket_start AS window_start,
                   bucket_end AS window_end,
                   count,
                   baseline,
                   metadata AS detail,
                   updated_at AS created_at,
                   updated_at
            FROM alert_windows
            {where}
            ORDER BY bucket_end DESC, count DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return {"data": [_alert_window_row(row) for row in rows], "total": len(rows)}


@router.get("/alerts/suppressions")
async def list_alert_suppressions(active_only: bool = True):
    where = "WHERE starts_at <= NOW() AND (ends_at IS NULL OR ends_at >= NOW())" if active_only else ""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id::text, scope, alert_type, entity_id::text, source, reason,
                   starts_at, ends_at, created_at
            FROM alert_suppressions
            {where}
            ORDER BY created_at DESC
            LIMIT 200
            """
        )
    return {"data": [_decode_alert_suppression(row) for row in rows], "total": len(rows)}


@router.post("/alerts/suppressions")
async def create_alert_suppression(payload: AlertSuppressionIn):
    if not payload.reason.strip():
        raise HTTPException(status_code=400, detail="reason is required")
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO alert_suppressions (
                scope, alert_type, entity_id, source, reason, starts_at, ends_at, created_at
            )
            VALUES (
                $1, $2, $3::uuid, $4, $5,
                COALESCE($6::timestamptz, NOW()),
                $7::timestamptz,
                NOW()
            )
            RETURNING id::text, scope, alert_type, entity_id::text, source, reason,
                      starts_at, ends_at, created_at
            """,
            payload.scope,
            payload.alert_type,
            payload.entity_id,
            payload.source,
            payload.reason,
            payload.starts_at,
            payload.ends_at,
        )
    return _decode_alert_suppression(row)


@router.patch("/alerts/suppressions/{suppression_id}")
async def update_alert_suppression(suppression_id: str, payload: AlertSuppressionPatch):
    if payload.reason is not None and not payload.reason.strip():
        raise HTTPException(status_code=400, detail="reason cannot be empty")
    if payload.status is not None and payload.status not in {"active", "expired"}:
        raise HTTPException(status_code=400, detail="status must be active or expired")
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE alert_suppressions
            SET reason = COALESCE($2, reason),
                ends_at = CASE
                    WHEN $4 = 'expired' THEN NOW()
                    WHEN $4 = 'active' AND $3::timestamptz IS NULL THEN NULL
                    ELSE COALESCE($3::timestamptz, ends_at)
                END
            WHERE id = $1::uuid
            RETURNING id::text, scope, alert_type, entity_id::text, source, reason,
                      starts_at, ends_at, created_at
            """,
            suppression_id,
            payload.reason,
            payload.ends_at,
            payload.status,
        )
    if not row:
        raise HTTPException(status_code=404, detail="suppression not found")
    return _decode_alert_suppression(row)


@router.delete("/alerts/suppressions/{suppression_id}")
async def expire_alert_suppression(suppression_id: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE alert_suppressions
            SET ends_at = NOW()
            WHERE id = $1::uuid
            RETURNING id::text, scope, alert_type, entity_id::text, source, reason,
                      starts_at, ends_at, created_at
            """,
            suppression_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="suppression not found")
    return {"ok": True, "suppression": _decode_alert_suppression(row)}


def _stream_alert_row(row) -> dict:
    return {
        "fingerprint": row["fingerprint"],
        "alert_type": row["alert_type"],
        "entity_id": row["entity_id"],
        "source": row["source"],
        "window_start": row["window_start"].isoformat() if row["window_start"] else None,
        "window_end": row["window_end"].isoformat() if row["window_end"] else None,
        "last_sent_at": row["last_sent_at"].isoformat() if row["last_sent_at"] else None,
        "count": row["count"],
        "status": row["status"],
        "detail": _decode_jsonb(row["detail"], {}),
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _alert_window_row(row) -> dict:
    return {
        "bucket_type": row["bucket_type"],
        "bucket_key": row["bucket_key"],
        "source": row["source"],
        "window_start": row["window_start"].isoformat() if row["window_start"] else None,
        "window_end": row["window_end"].isoformat() if row["window_end"] else None,
        "count": row["count"],
        "baseline": float(row["baseline"]) if row["baseline"] is not None else None,
        "detail": _decode_jsonb(row["detail"], {}),
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _decode_alert_suppression(row) -> dict:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "alert_type": row["alert_type"],
        "entity_id": row["entity_id"],
        "source": row["source"],
        "reason": row["reason"],
        "starts_at": row["starts_at"].isoformat() if row["starts_at"] else None,
        "ends_at": row["ends_at"].isoformat() if row["ends_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
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


@router.get("/runs/{run_id}/phases")
async def get_run_phases(run_id: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT phase, status, duration_ms, error, created_at
            FROM run_phase_status
            WHERE run_id = $1::uuid
            ORDER BY created_at, phase
        """, run_id)
    return {
        "run_id": run_id,
        "phases": [
            {
                "phase": r["phase"],
                "status": r["status"],
                "duration_ms": r["duration_ms"],
                "error": r["error"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/notifications/audit")
async def list_notification_audit(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    message_type: str | None = None,
    status: str | None = None,
):
    pool = get_analyzer_pool()
    offset = (page - 1) * per_page
    conditions: list[str] = []
    params: list = []
    idx = 1

    if message_type:
        conditions.append(f"message_type = ${idx}")
        params.append(message_type)
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM notification_audit {where}", *params)
        params.extend([per_page, offset])
        rows = await conn.fetch(f"""
            SELECT id, channel, chat_id, message_type, text_preview, status,
                   telegram_message_id, related_run_id, related_alert_id,
                   error, created_at
            FROM notification_audit
            {where}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """, *params)

    return {
        "data": [
            {
                "id": str(r["id"]),
                "channel": r["channel"],
                "chat_id": r["chat_id"],
                "message_type": r["message_type"],
                "text_preview": r["text_preview"],
                "status": r["status"],
                "telegram_message_id": r["telegram_message_id"],
                "related_run_id": str(r["related_run_id"]) if r["related_run_id"] else None,
                "related_alert_id": str(r["related_alert_id"]) if r["related_alert_id"] else None,
                "error": r["error"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.post("/runs/trigger")
async def trigger_run():
    from src.pipeline.incremental_runner import run_incremental

    try:
        stats = await run_incremental()
        return {"ok": True, "stats": stats}
    except Exception as e:
        raise HTTPException(500, f"Run failed: {e}")
