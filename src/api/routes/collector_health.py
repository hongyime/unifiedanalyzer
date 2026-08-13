import logging
import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
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


async def _fetch_collector_dashboard_endpoint(path: str, *, timeout: float | None = None) -> dict[str, Any]:
    url = _collector_dashboard_url() + path
    request_timeout = timeout if timeout is not None else float(os.getenv("COLLECTOR_DASHBOARD_TIMEOUT_SECONDS", "4"))
    try:
        payload = await asyncio.to_thread(_fetch_json_sync, url, request_timeout)
        return {"reachable": True, "available": bool(payload.get("available", True)), "payload": payload}
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.info("collector dashboard endpoint unavailable %s: %s", path, exc.__class__.__name__)
        return {"reachable": False, "available": False, "error": exc.__class__.__name__, "payload": None}


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


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError):
        if isinstance(row, dict):
            return row.get(key, default)
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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


def _collector_production_summary(surfaces: dict[str, dict[str, Any]]) -> dict[str, Any]:
    instagram = _as_dict(surfaces.get("instagram_health", {}).get("payload"))
    domain = _as_dict(surfaces.get("domain_pacing", {}).get("payload"))
    quotas = _as_dict(surfaces.get("api_quotas", {}).get("payload"))
    realtime = _as_dict(surfaces.get("realtime_feed", {}).get("payload"))
    rollout = _as_dict(surfaces.get("optional_rollout", {}).get("payload"))

    quota_snapshots = _as_list(quotas.get("snapshots"))
    paused_quotas = [row for row in quota_snapshots if isinstance(row, dict) and row.get("paused")]
    source_counters = _as_dict(realtime.get("source_counters"))
    realtime_failed_sources = []
    for source, counters in source_counters.items():
        values = _as_dict(counters)
        failed = _int_value(values.get("failed"))
        too_large = _int_value(values.get("too_large"))
        local_fallback = _int_value(values.get("local_fallback"))
        if failed or too_large or local_fallback:
            realtime_failed_sources.append({
                "source": source,
                "failed": failed,
                "too_large": too_large,
                "local_fallback": local_fallback,
            })

    domain_sources = [row for row in _as_list(domain.get("sources")) if isinstance(row, dict)]
    return {
        "instagram_stuck_stage": instagram.get("stuck_stage"),
        "instagram_cooldown_active": bool(_as_dict(instagram.get("cooldown")).get("active")),
        "realtime_queue_depth": _int_value(realtime.get("queue_depth")),
        "realtime_failed_sources": realtime_failed_sources,
        "domain_pacing_sources": len(domain_sources),
        "domain_robots_blocked": sum(_int_value(row.get("robots_blocked")) for row in domain_sources),
        "domain_429": sum(_int_value(row.get("http_429")) for row in domain_sources),
        "quota_snapshots": len(quota_snapshots),
        "quota_paused": len(paused_quotas),
        "optional_rollout_action": rollout.get("recommended_action"),
        "optional_rollout_can_proceed": rollout.get("can_proceed"),
    }


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


@router.get("/collector/production-status")
async def collector_production_status():
    endpoints = {
        "instagram_health": "/instagram/health",
        "domain_pacing": "/domain-pacing/status",
        "api_quotas": "/api-quotas/status",
        "realtime_feed": "/media/realtime-feed/status",
        "optional_rollout": "/optional-rollout/status?feature=spiderfoot&stage=dry-run",
    }
    timeout = float(os.getenv("COLLECTOR_DASHBOARD_PRODUCTION_TIMEOUT_SECONDS", "15"))
    fetched = await asyncio.gather(*[
        _fetch_collector_dashboard_endpoint(path, timeout=timeout)
        for path in endpoints.values()
    ])
    surfaces = dict(zip(endpoints.keys(), fetched))
    reachable = sum(1 for item in surfaces.values() if item.get("reachable"))
    return {
        "collector_dashboard": "ok" if reachable else "unreachable",
        "surfaces": surfaces,
        "summary": _collector_production_summary(surfaces),
    }


@router.get("/collector/coverage")
async def collector_coverage():
    try:
        pool = get_collector_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (source)
                       *, expected_cadence::text AS expected_cadence_text
                FROM collection_coverage_snapshots
                ORDER BY source, created_at DESC
                """
            )
    except Exception as exc:  # noqa: BLE001 - collector is optional for analyzer uptime
        return {"collector_db": "unreachable", "sources": [], "total": 0, "summary": {"total": 0, "fresh": 0, "degraded": 0, "stale": 0}, "error": str(exc)[:300]}
    sources = []
    for row in rows:
        sources.append({
            "source": row["source"],
            "expected_cadence": _row_get(row, "expected_cadence_text", row["expected_cadence"]),
            "latest_data_at": row["latest_data_at"].isoformat() if row["latest_data_at"] else None,
            "latest_run_at": row["latest_run_at"].isoformat() if row["latest_run_at"] else None,
            "status": row["status"],
            "rows_24h": row["rows_24h"],
            "media_24h": row["media_24h"],
            "errors_24h": row["errors_24h"],
            "rate_limits_24h": row["rate_limits_24h"],
            "private_access_failures": row["private_access_failures"],
            "stale_targets": row["stale_targets"],
            "seen_targets_total": int(_row_get(row, "seen_targets_total", 0) or 0),
            "seen_targets_backfilled": int(_row_get(row, "seen_targets_backfilled", 0) or 0),
            "seen_targets_pending": int(_row_get(row, "seen_targets_pending", 0) or 0),
            "seen_targets_fresh": int(_row_get(row, "seen_targets_fresh", 0) or 0),
            "seen_targets_stale": int(_row_get(row, "seen_targets_stale", 0) or 0),
            "seen_targets_newly_discovered": int(_row_get(row, "seen_targets_newly_discovered", 0) or 0),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })
    fresh = sum(1 for row in sources if row["status"] == "fresh")
    degraded = sum(1 for row in sources if row["status"] == "degraded")
    stale = sum(1 for row in sources if row["status"] == "stale")
    latest_snapshot = max((row["created_at"] for row in rows if row["created_at"]), default=None)
    latest_snapshot_iso = latest_snapshot.isoformat() if latest_snapshot else None
    snapshot_age_seconds = None
    if latest_snapshot:
        now = datetime.now(timezone.utc)
        snapshot_age_seconds = int((now - latest_snapshot.astimezone(timezone.utc)).total_seconds())
    snapshot_stale_after_seconds = 2 * 60 * 60
    return {
        "collector_db": "connected",
        "sources": sources,
        "total": len(sources),
        "summary": {"total": len(sources), "fresh": fresh, "degraded": degraded, "stale": stale},
        "snapshot_created_at": latest_snapshot_iso,
        "snapshot_age_seconds": snapshot_age_seconds,
        "snapshot_stale": bool(snapshot_age_seconds is not None and snapshot_age_seconds > snapshot_stale_after_seconds),
    }
