import json
import hashlib
import os
import asyncpg
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from src.db.connection import get_analyzer_pool
from src.pipeline.indicator_export import _supabase_database_url, supabase_export_config

router = APIRouter(tags=["export"])


async def _table_exists(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table))


def _hash_value(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


async def _supabase_remote_readback(config: dict) -> dict:
    """Return compact proof that the remote Supabase table is reachable/populated."""
    dsn = _supabase_database_url()
    if not dsn:
        return {"configured": False, "reachable": False, "reason": "database_url_missing"}
    timeout = float(os.getenv("SUPABASE_STATUS_READBACK_TIMEOUT_SECONDS", "45"))
    conn = None
    try:
        conn = await asyncpg.connect(dsn, timeout=timeout)
        exists = await conn.fetchval("SELECT to_regclass('public.normalized_indicators') IS NOT NULL")
        if not exists:
            return {"configured": True, "reachable": True, "table_exists": False}
        row = await conn.fetchrow(
            """
            SELECT count(*)::bigint AS row_count,
                   max(exported_at) AS latest_exported_at
            FROM public.normalized_indicators
            """
        )
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": int(row["row_count"] or 0),
            "latest_exported_at": row["latest_exported_at"].isoformat() if row["latest_exported_at"] else None,
            "write_method": config.get("write_method"),
        }
    except Exception as exc:  # noqa: BLE001 - status endpoint must report drift, not fail
        return {
            "configured": True,
            "reachable": False,
            "error": exc.__class__.__name__,
        }
    finally:
        if conn is not None:
            await conn.close()


@router.get("/entities/{entity_id}/export")
async def export_entity(entity_id: str, format: str = "json"):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            "SELECT * FROM entities WHERE id = $1::uuid", entity_id
        )
        if not entity:
            raise HTTPException(404, "Entity not found")

        links = await conn.fetch("""
            SELECT source, platform_id, platform_username, platform_name,
                   confidence, link_method, is_confirmed
            FROM entity_platform_links WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
        """, entity_id)

        signals = await conn.fetch("""
            SELECT signal_type, source_platform, target_platform, value, confidence
            FROM identity_signals WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
        """, entity_id)

        events = await conn.fetch("""
            SELECT source, event_type, source_record_id, occurred_at, title
            FROM timeline_events WHERE entity_id = $1::uuid
            ORDER BY occurred_at DESC LIMIT 500
        """, entity_id)

        behavior = await conn.fetchrow(
            "SELECT * FROM behavioral_profiles WHERE entity_id = $1::uuid", entity_id
        )

    data = {
        "entity": {
            "id": str(entity["id"]),
            "canonical_name": entity["canonical_name"],
            "tier": entity["tier"],
            "confidence_score": entity["confidence_score"],
            "signal_count": entity["signal_count"],
        },
        "platform_links": [
            {
                "source": l["source"],
                "platform_id": l["platform_id"],
                "platform_username": l["platform_username"],
                "platform_name": l["platform_name"],
                "confidence": l["confidence"],
                "link_method": l["link_method"],
                "is_confirmed": l["is_confirmed"],
            }
            for l in links
        ],
        "identity_signals": [
            {
                "signal_type": s["signal_type"],
                "source_platform": s["source_platform"],
                "target_platform": s["target_platform"],
                "value": s["value"],
                "confidence": s["confidence"],
            }
            for s in signals
        ],
        "timeline_events": [
            {
                "source": e["source"],
                "event_type": e["event_type"],
                "source_record_id": e["source_record_id"],
                "occurred_at": e["occurred_at"].isoformat() if e["occurred_at"] else None,
                "title": e["title"],
            }
            for e in events
        ],
        "behavioral_profile": {
            "posting_hour_dist": behavior["posting_hour_dist"],
            "posting_dow_dist": behavior["posting_dow_dist"],
            "avg_post_interval_days": behavior["avg_post_interval_days"],
            "total_events": behavior["total_events"],
        } if behavior else None,
    }

    name = entity["canonical_name"] or entity_id
    content = json.dumps(data, indent=2, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}_export.json"'},
    )


@router.get("/identity/truth/status")
async def identity_truth_status():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "identity_truth_assertions"):
            return {"status": "schema_pending", "total": 0, "by_state": [], "recent": []}
        total = await conn.fetchval("SELECT count(*) FROM identity_truth_assertions")
        by_state = await conn.fetch(
            """
            SELECT truth_state, count(*)::bigint AS count,
                   round(avg(confidence)::numeric, 3) AS avg_confidence
            FROM identity_truth_assertions
            GROUP BY truth_state
            ORDER BY count DESC
            """
        )
        recent = await conn.fetch(
            """
            SELECT assertion_type, entity_id::text, value, truth_state,
                   confidence, evidence_count, source_platform, updated_at
            FROM identity_truth_assertions
            ORDER BY updated_at DESC
            LIMIT 20
            """
        )
    return {
        "status": "ok",
        "total": int(total or 0),
        "by_state": [
            {
                "truth_state": row["truth_state"],
                "count": int(row["count"] or 0),
                "avg_confidence": float(row["avg_confidence"] or 0),
            }
            for row in by_state
        ],
        "policy": {
            "spiderfoot_truth": "weak_lead_only",
            "auto_truth_requires": "independent_hard_signal_corroboration",
        },
        "recent": [
            {
                "assertion_type": row["assertion_type"],
                "entity_id": row["entity_id"],
                "value_hash": _hash_value(row["value"]),
                "truth_state": row["truth_state"],
                "confidence": float(row["confidence"] or 0),
                "evidence_count": int(row["evidence_count"] or 0),
                "source_platform": row["source_platform"],
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
            for row in recent
        ],
    }


@router.get("/indicators/export/supabase/status")
async def supabase_indicator_export_status():
    pool = get_analyzer_pool()
    config = supabase_export_config()
    remote_readback = await _supabase_remote_readback(config)
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "normalized_indicators"):
            return {
                "status": "schema_pending",
                "config": config,
                "counts": [],
                "ready_to_export": 0,
                "raw_mirror": False,
                "remote_readback": remote_readback,
            }
        counts = await conn.fetch(
            """
            SELECT indicator_type, export_status, supabase_exportable,
                   count(*)::bigint AS count
            FROM normalized_indicators
            GROUP BY indicator_type, export_status, supabase_exportable
            ORDER BY indicator_type, export_status, supabase_exportable DESC
            """
        )
        ready = await conn.fetchval(
            """
            SELECT count(*)
            FROM normalized_indicators
            WHERE supabase_exportable = TRUE
              AND export_status IN ('pending', 'retry')
            """
        )
    return {
        "status": "ok",
        "config": config,
        "counts": [
            {
                "indicator_type": row["indicator_type"],
                "export_status": row["export_status"],
                "supabase_exportable": bool(row["supabase_exportable"]),
                "count": int(row["count"] or 0),
            }
            for row in counts
        ],
        "ready_to_export": int(ready or 0),
        "raw_mirror": False,
        "remote_readback": remote_readback,
        "supabase_free_tier_guard": {
            "db_budget_mb": 500,
            "storage_budget_gb": 1,
            "strategy": "compact_normalized_rows_only",
            "raw_private_text_exported": False,
        },
    }


@router.get("/indicators/export/supabase/preview")
async def supabase_indicator_export_preview(limit: int = 25):
    pool = get_analyzer_pool()
    capped = max(1, min(int(limit or 25), 100))
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "normalized_indicators"):
            return {"status": "schema_pending", "items": []}
        rows = await conn.fetch(
            """
            SELECT indicator_type, normalized_value, source_families,
                   evidence_count, confidence, export_status, updated_at
            FROM normalized_indicators
            WHERE supabase_exportable = TRUE
            ORDER BY updated_at DESC
            LIMIT $1
            """,
            capped,
        )
    return {
        "status": "ok",
        "items": [
            {
                "indicator_type": row["indicator_type"],
                "value_hash": _hash_value(row["normalized_value"]),
                "source_families": list(row["source_families"] or []),
                "evidence_count": int(row["evidence_count"] or 0),
                "confidence": float(row["confidence"] or 0),
                "export_status": row["export_status"],
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
            for row in rows
        ],
    }
