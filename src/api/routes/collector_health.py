import logging
import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from src.db.connection import get_collector_pool

router = APIRouter(tags=["collector-health"])
logger = logging.getLogger(__name__)


def _collector_dashboard_url() -> str:
    return os.getenv("COLLECTOR_DASHBOARD_URL", "http://unifiedcollector_dashboard:8700").rstrip("/")


def _fetch_json_sync(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local dashboard URL
        raw = resp.read(8 * 1024 * 1024)
    payload = json.loads(raw.decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


async def _fetch_collector_source_matrix() -> dict[str, Any] | None:
    url = _collector_dashboard_url() + "/collectors/source-matrix"
    timeout = float(os.getenv(
        "COLLECTOR_DASHBOARD_SOURCE_MATRIX_TIMEOUT_SECONDS",
        os.getenv("COLLECTOR_DASHBOARD_TIMEOUT_SECONDS", "4"),
    ))
    try:
        return await asyncio.to_thread(_fetch_json_sync, url, timeout)
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.info("collector dashboard source-matrix unavailable: %s", exc.__class__.__name__)
        return None


async def _fetch_collector_live() -> dict[str, Any] | None:
    url = _collector_dashboard_url() + "/collectors/live"
    timeout = float(os.getenv("COLLECTOR_DASHBOARD_LIVE_TIMEOUT_SECONDS", "4"))
    try:
        return await asyncio.to_thread(_fetch_json_sync, url, timeout)
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.info("collector dashboard live unavailable: %s", exc.__class__.__name__)
        return None


async def _acquire_collector_conn(pool):
    timeout = float(os.getenv("ANALYZER_COLLECTOR_HEALTH_DB_ACQUIRE_TIMEOUT_SECONDS", "3"))
    return await asyncio.wait_for(pool.acquire(), timeout=timeout)


def _latest_iso(*values: Any) -> str | None:
    parsed: list[tuple[datetime, str]] = []
    for value in values:
        if not value:
            continue
        text = str(value)
        try:
            parsed.append((datetime.fromisoformat(text.replace("Z", "+00:00")), text))
        except ValueError:
            parsed.append((datetime.min, text))
    if not parsed:
        return None
    return max(parsed, key=lambda item: item[0])[1]


def _targets_by_source(targets: list[Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for t in targets:
        src = t["source"]
        out.setdefault(src, []).append({
            "status": t["status"],
            "count": t["count"],
            "last_collection": t["last_collection"].isoformat() if t["last_collection"] else None,
        })
    return out


def _collector_from_matrix_row(row: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any]:
    last_24h = row.get("last_24h") or {}
    current_hour = row.get("current_hour") or {}
    blocker = row.get("blocker") or {}
    media_freshness = row.get("media_freshness") or {}
    last_completed = _latest_iso(
        last_24h.get("latest_record_at"),
        last_24h.get("latest_media_at"),
        last_24h.get("latest_event_at"),
        row.get("latest_media_at"),
    )
    records_24h = int(last_24h.get("records") or 0)
    media_24h = int(last_24h.get("media_items") or 0)
    messages_24h = int(last_24h.get("messages") or 0)
    rate_limits_24h = int(last_24h.get("rate_limits") or 0)
    access_errors_24h = int(last_24h.get("access_errors") or 0)
    return {
        "source": row.get("source"),
        "display_name": row.get("display_name"),
        "status": row.get("status"),
        "collection_mode": row.get("collection_mode"),
        "last_started": last_completed,
        "last_completed": last_completed,
        "items_24h": records_24h + media_24h,
        "records_24h": records_24h,
        "messages_24h": messages_24h,
        "media_24h": media_24h,
        "failed_24h": rate_limits_24h + access_errors_24h,
        "rate_limits_24h": rate_limits_24h,
        "access_errors_24h": access_errors_24h,
        "runs_24h": int(last_24h.get("runs") or 0),
        "current_hour": current_hour,
        "latest_status": row.get("status"),
        "blocker": blocker,
        "media_freshness": media_freshness,
        "targets": targets,
    }


def _collectors_from_source_matrix(matrix: dict[str, Any], targets: list[Any]) -> list[dict[str, Any]]:
    by_source = _targets_by_source(targets)
    rows = matrix.get("sources") or []
    collectors = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("source"):
            continue
        collectors.append(_collector_from_matrix_row(row, by_source.get(row["source"], [])))
    collectors.sort(key=lambda c: str(c.get("source") or ""))
    return collectors


def _collector_from_live_row(row: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any]:
    last_seen = _latest_iso(
        row.get("source_health_last_success_at"),
        row.get("source_health_updated_at"),
        row.get("browser_heartbeat_at"),
    )
    return {
        "source": row.get("source"),
        "display_name": row.get("source"),
        "status": row.get("status"),
        "collection_mode": row.get("collection_mode"),
        "last_started": last_seen,
        "last_completed": last_seen,
        "items_24h": 0,
        "records_24h": 0,
        "messages_24h": 0,
        "media_24h": 0,
        "failed_24h": 0,
        "rate_limits_24h": 0,
        "access_errors_24h": 0,
        "runs_24h": 0,
        "current_hour": {},
        "latest_status": row.get("status"),
        "blocker": {
            "kind": row.get("bridge_status") or row.get("status"),
            "detail": row.get("bridge_detail") or row.get("detail"),
        },
        "media_freshness": {},
        "targets": targets,
    }


def _collectors_from_live(live: dict[str, Any], targets: list[Any]) -> list[dict[str, Any]]:
    by_source = _targets_by_source(targets)
    collectors = []
    for row in live.get("sources") or []:
        if not isinstance(row, dict) or not row.get("source"):
            continue
        collectors.append(_collector_from_live_row(row, by_source.get(row["source"], [])))
    collectors.sort(key=lambda c: str(c.get("source") or ""))
    return collectors


async def _fetch_collector_db_snapshot() -> tuple[list[Any], list[Any]]:
    pool = get_collector_pool()
    conn = None
    try:
        conn = await _acquire_collector_conn(pool)
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
        """, timeout=8)

        targets = await conn.fetch("""
            SELECT source, status, COUNT(*) AS count,
                   MAX(last_collection_at) AS last_collection
            FROM collection_targets
            GROUP BY source, status
            ORDER BY source
        """, timeout=8)
        return list(runs), list(targets)
    finally:
        if conn is not None:
            await pool.release(conn)


@router.get("/collector/health")
async def collector_health():
    matrix = await _fetch_collector_source_matrix()
    live = None if matrix else await _fetch_collector_live()
    try:
        runs, targets = await _fetch_collector_db_snapshot()
    except Exception as e:  # noqa: BLE001 - collector health is optional for analyzer uptime
        logger.warning("collector health skipped: %s", e)
        if matrix:
            return {
                "collectors": _collectors_from_source_matrix(matrix, []),
                "collector_db": "unreachable",
                "collector_dashboard": "ok",
                "source": "collector_dashboard",
                "error": str(e)[:300],
            }
        if live:
            return {
                "collectors": _collectors_from_live(live, []),
                "collector_db": "unreachable",
                "collector_dashboard": "ok",
                "source": "collector_live",
                "error": str(e)[:300],
            }
        return {
            "collectors": [],
            "collector_db": "unreachable",
            "collector_dashboard": "unreachable",
            "error": str(e)[:300],
        }

    if matrix:
        return {
            "collectors": _collectors_from_source_matrix(matrix, list(targets)),
            "collector_db": "connected",
            "collector_dashboard": "ok",
            "source": "collector_dashboard",
            "generated_at": matrix.get("generated_at"),
        }

    if live:
        return {
            "collectors": _collectors_from_live(live, list(targets)),
            "collector_db": "connected",
            "collector_dashboard": "ok",
            "source": "collector_live",
        }

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

    return {
        "collectors": result,
        "collector_db": "connected",
        "collector_dashboard": "unreachable",
        "source": "collector_db",
    }
