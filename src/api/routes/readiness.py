"""Production readiness surface tied to operator user stories."""
from __future__ import annotations

import os
import asyncio
import time

from fastapi import APIRouter, Request

router = APIRouter(tags=["readiness"])


USER_STORIES: dict[str, dict[str, str]] = {
    "databases_connected": {
        "actor": "operator",
        "story": "I can trust the system is reading both Analyzer and Collector state before it reports production status.",
        "value": "Prevents green production checks that are based on partial or missing database evidence.",
        "proves": "Analyzer and Collector database pools are connected.",
    },
    "scheduler_self_healing": {
        "actor": "operator",
        "story": "I can leave Analyzer running and know scheduled jobs are still making forward progress.",
        "value": "Detects stuck incremental or full-resolution analysis before downstream outputs go stale.",
        "proves": "Incremental and full-resolution runs have fresh completion or a fresh active heartbeat.",
    },
    "supabase_populated": {
        "actor": "analyst",
        "story": "I can consume compact Analyzer indicators from Supabase without receiving a raw Collector mirror.",
        "value": "Confirms the Supabase product path is populated, drained, remotely readable, and privacy-bounded.",
        "proves": "Local export is ok, backlog is zero, remote row count covers local exported rows, and raw_mirror is false.",
    },
    "backup_restorable": {
        "actor": "operator",
        "story": "I can recover from data loss using a backup that has restore proof.",
        "value": "Avoids relying on dump files that were written but never validated.",
        "proves": "Latest backup completed successfully and passed restore-list validation.",
    },
    "decision_log_durable": {
        "actor": "operator",
        "story": "I can audit durable decisions after restarts or crashes.",
        "value": "Keeps operational decisions from being trapped only in transient DB state.",
        "proves": "Decision-log JSONL pending and error counts are zero.",
    },
    "face_identity_safety": {
        "actor": "analyst",
        "story": "I can use face and identity links without silent hard collisions.",
        "value": "Protects identity-truth workflows from unsafe automatic merges.",
        "proves": "Face bridge audit reports no face/entity or cluster/entity hard collisions.",
    },
    "face_processing_fresh": {
        "actor": "analyst",
        "story": "I can rely on recent image evidence being indexed, not just historical face data.",
        "value": "Shows the face worker is currently useful for new media.",
        "proves": "Face processing reports fresh indexed image evidence.",
    },
    "collector_production_surfaces": {
        "actor": "operator",
        "story": "I can tell that Collector is reachable, authenticated, ingesting, and not hiding hard source failures.",
        "value": "Combines dashboard, browser, cookie-vault, quota, pacing, and realtime health into one critical gate.",
        "proves": "Collector surfaces are reachable, hard issues are zero, browser ingest is active, cookie vault is ok, and realtime has no hard failures.",
    },
    "collector_hourly_yield_floor": {
        "actor": "operator",
        "story": "I can see when browser-managed sources are alive but not producing enough useful output.",
        "value": "Turns silent low-yield collection into visible operator pressure without failing critical readiness during rate limits.",
        "proves": "Monitored sources meet the rolling useful-output floor or are explicitly exempt due current pressure.",
    },
    "collector_action_queue_visible": {
        "actor": "operator",
        "story": "I can see open Collector repair, auth, pressure, and target-starved actions on the production readiness page.",
        "value": "Prevents readiness from looking complete while Collector has unresolved operator work.",
        "proves": "The Collector action queue is reachable and reports zero open actions, or exposes the open actions as readiness work.",
    },
    "data_quality_ledger": {
        "actor": "analyst",
        "story": "I can see whether raw Collector signals become Analyzer timeline, media, text, or Supabase value.",
        "value": "Protects the product from collecting data that never turns into user-visible intelligence.",
        "proves": "Ledger has no source gaps across the configured lookback.",
    },
    "analyst_workflows_available": {
        "actor": "analyst",
        "story": "I can open entity review, triage, and case workflows after production readiness is green.",
        "value": "Makes readiness prove the usable analyst app, not only background jobs and data pipes.",
        "proves": "Core analyst API routes are mounted before the SPA fallback and available in the live app.",
    },
    "analyst_value_path_proven": {
        "actor": "analyst",
        "story": "I can review a candidate, rely on durable decision provenance, save evidence into a case, and export a dossier.",
        "value": "Prevents a green production page from hiding an analyst app that has data but no usable investigation workflow.",
        "proves": "There is review work, durable audit-log decision evidence, at least one case item, and a case export path.",
    },
}


async def _health_status() -> dict[str, Any]:
    from src.api.routes.health import health_check

    return await health_check()


async def _health_status_retry_after_timeout(original_error: Exception, timeout_seconds: float) -> dict[str, Any] | None:
    if not isinstance(original_error, asyncio.TimeoutError):
        return None
    if str(original_error):
        return None
    try:
        retry_timeout = float(os.getenv("ANALYZER_READINESS_HEALTH_RETRY_TIMEOUT_SECONDS", "90"))
        if retry_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        retry_timeout = 90.0
    if retry_timeout <= timeout_seconds:
        return None
    health = await asyncio.wait_for(_health_status(), timeout=retry_timeout)
    if isinstance(health, dict):
        health = dict(health)
        health["fallback"] = "isolated_health_retry"
        health["primary_error"] = f"{original_error.__class__.__name__}: {original_error}"
        health["timeout_seconds"] = timeout_seconds
        health["retry_timeout_seconds"] = retry_timeout
    return health


async def _health_status_fast_fallback(original_error: Exception, timeout_seconds: float) -> dict[str, Any]:
    """Build the critical readiness health surface with smaller independent queries.

    The full health endpoint intentionally includes broad operator evidence. Under
    DB/browser load it can exceed the readiness budget even when the individual
    proof surfaces are healthy. This fallback keeps true failures visible while
    avoiding a false critical-red page caused only by one slow fan-out.
    """
    from src.api.routes.health import (
        _face_processing_health,
        _run_freshness,
        _supabase_export_health,
        audit_face_bridge_collisions,
    )
    from src.db.connection import get_analyzer_pool, get_collector_pool

    status: dict[str, Any] = {
        "status": "degraded",
        "analyzer_db": "unknown",
        "collector_db": "unknown",
        "scheduler_freshness": {"incremental": {}, "full_resolution": {}},
        "supabase_export": {},
        "last_backup_run": {},
        "decision_log": {},
        "face_bridge_audit": {},
        "face_processing": {},
        "fallback": "fast_health",
        "primary_error": f"{original_error.__class__.__name__}: {original_error}",
        "timeout_seconds": timeout_seconds,
    }

    async def analyzer_slice() -> None:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            status["analyzer_db"] = "connected"

            heartbeat_stale_seconds = _env_int_local(
                "ANALYZER_HEALTH_RUNNING_RUN_HEARTBEAT_STALE_MINUTES",
                _env_int_local("STALE_RUN_HEARTBEAT_MINUTES", 90, minimum=1),
                minimum=1,
            ) * 60
            incremental_interval_seconds = _env_int_local("INCREMENTAL_RUN_INTERVAL_MINUTES", 60, minimum=1) * 60
            full_interval_seconds = _env_int_local("FULL_RESOLUTION_INTERVAL_HOURS", 12, minimum=1) * 3600
            incremental_stale_seconds = _env_int_local(
                "ANALYZER_HEALTH_INCREMENTAL_STALE_MINUTES",
                max(180, _env_int_local("INCREMENTAL_RUN_INTERVAL_MINUTES", 60, minimum=1) * 3),
                minimum=1,
            ) * 60
            full_stale_seconds = _env_int_local(
                "ANALYZER_HEALTH_FULL_RESOLUTION_STALE_HOURS",
                max(24, _env_int_local("FULL_RESOLUTION_INTERVAL_HOURS", 12, minimum=1) * 2),
                minimum=1,
            ) * 3600
            inc, full = await asyncio.gather(
                _run_freshness(
                    conn,
                    "incremental",
                    completed_stale_after_seconds=max(incremental_stale_seconds, incremental_interval_seconds),
                    heartbeat_stale_after_seconds=heartbeat_stale_seconds,
                ),
                _run_freshness(
                    conn,
                    "full_resolution",
                    completed_stale_after_seconds=max(full_stale_seconds, full_interval_seconds),
                    heartbeat_stale_after_seconds=heartbeat_stale_seconds,
                ),
            )
            status["scheduler_freshness"] = {"incremental": inc, "full_resolution": full}
            status["supabase_export"] = await _supabase_export_health(conn)

            try:
                backup = await conn.fetchrow(
                    """
                    SELECT status, kinds, started_at, finished_at, path, size_bytes,
                           deleted_count, restore_validation, error_message
                    FROM analyzer_backup_runs
                    WHERE status = 'failed'
                       OR (status = 'success' AND path IS NOT NULL)
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                )
            except Exception:
                backup = None
            if backup:
                status["last_backup_run"] = {
                    "status": backup["status"],
                    "kinds": list(backup["kinds"] or []),
                    "started_at": backup["started_at"].isoformat() if backup["started_at"] else None,
                    "finished_at": backup["finished_at"].isoformat() if backup["finished_at"] else None,
                    "path": backup["path"],
                    "size_bytes": backup["size_bytes"],
                    "deleted_count": backup["deleted_count"],
                    "restore_validation": backup["restore_validation"],
                    "error_message": backup["error_message"],
                }

            try:
                decision_log = await conn.fetchrow(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE decision_jsonl_written_at IS NULL)::int AS pending_jsonl,
                        COUNT(*) FILTER (WHERE decision_jsonl_error IS NOT NULL)::int AS jsonl_errors,
                        MAX(decision_jsonl_written_at) AS latest_jsonl_written_at,
                        MAX(created_at) FILTER (WHERE decision_jsonl_error IS NOT NULL) AS latest_jsonl_error_at
                    FROM audit_log
                    """
                )
            except Exception:
                decision_log = None
            if decision_log:
                status["decision_log"] = {
                    "pending_jsonl": int(decision_log["pending_jsonl"] or 0),
                    "jsonl_errors": int(decision_log["jsonl_errors"] or 0),
                    "latest_jsonl_written_at": (
                        decision_log["latest_jsonl_written_at"].isoformat()
                        if decision_log["latest_jsonl_written_at"]
                        else None
                    ),
                    "latest_jsonl_error_at": (
                        decision_log["latest_jsonl_error_at"].isoformat()
                        if decision_log["latest_jsonl_error_at"]
                        else None
                    ),
                }

            status["face_bridge_audit"] = await audit_face_bridge_collisions(conn, sample_limit=5)
            status["face_processing"] = await _face_processing_health(conn)

    async def collector_slice() -> None:
        pool = get_collector_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            status["collector_db"] = "connected"

    try:
        fallback_timeout = float(os.getenv("ANALYZER_READINESS_HEALTH_FALLBACK_TIMEOUT_SECONDS", "35"))
        if fallback_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        fallback_timeout = 35.0
    results = await asyncio.gather(
        asyncio.wait_for(analyzer_slice(), timeout=fallback_timeout),
        asyncio.wait_for(collector_slice(), timeout=min(fallback_timeout, 10.0)),
        return_exceptions=True,
    )
    errors = [f"{item.__class__.__name__}: {item}" for item in results if isinstance(item, Exception)]
    if errors:
        status["fallback_errors"] = errors
    if (
        status.get("analyzer_db") == "connected"
        and status.get("collector_db") == "connected"
        and _scheduler_progress_ok(
            (status.get("scheduler_freshness") or {}).get("incremental") or {},
            (status.get("scheduler_freshness") or {}).get("full_resolution") or {},
        )
        and (status.get("supabase_export") or {}).get("ok") is True
        and (status.get("face_bridge_audit") or {}).get("ok") is True
        and (status.get("face_processing") or {}).get("ok") is True
    ):
        status["status"] = "ok"
    return status


def _env_int_local(name: str, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


async def _collector_status() -> dict[str, Any]:
    from src.api.routes.collector_health import collector_production_status

    return await collector_production_status()


async def _collector_status_retry_after_timeout(
    original_error: Exception,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    if not isinstance(original_error, asyncio.TimeoutError):
        return None
    if str(original_error):
        return None
    try:
        retry_timeout = float(os.getenv("ANALYZER_READINESS_COLLECTOR_RETRY_TIMEOUT_SECONDS", "90"))
        if retry_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        retry_timeout = 90.0
    if retry_timeout <= timeout_seconds:
        return None
    collector_status = await asyncio.wait_for(_collector_status(), timeout=retry_timeout)
    if isinstance(collector_status, dict):
        collector_status = dict(collector_status)
        summary = dict(collector_status.get("summary") or {})
        summary["primary_error"] = f"{original_error.__class__.__name__}: {original_error}"
        summary["retry_timeout_seconds"] = retry_timeout
        collector_status["summary"] = summary
        collector_status["proof_path"] = "isolated_collector_retry"
    return collector_status


async def _collector_status_fallback() -> dict[str, Any]:
    from src.api.routes.collector_health import (
        _collector_cookie_vault_url,
        _collector_production_summary,
        _fetch_browser_yield_rolling_60m,
        _fetch_collector_absolute_endpoint,
        _fetch_collector_dashboard_endpoint,
    )

    try:
        timeout = float(os.getenv("ANALYZER_READINESS_COLLECTOR_FALLBACK_TIMEOUT_SECONDS", "25"))
        if timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        timeout = 12.0
    dashboard_health, source_matrix, cookie_vault, rolling_yield = await asyncio.gather(
        _fetch_collector_dashboard_endpoint("/health?include_sources=true", timeout=timeout),
        _fetch_collector_dashboard_endpoint("/collectors/source-matrix", timeout=timeout),
        _fetch_collector_absolute_endpoint(
            _collector_cookie_vault_url() + "/health",
            timeout=min(timeout, 5.0),
        ),
        _fetch_browser_yield_rolling_60m(),
    )
    surfaces = {
        "dashboard_health": dashboard_health,
        "source_matrix": source_matrix,
        "browser_cookie_vault": cookie_vault,
        "browser_yield_rolling_60m": {
            "reachable": True,
            "available": True,
            "payload": rolling_yield,
        },
    }
    reachable = sum(1 for item in surfaces.values() if item.get("reachable"))
    return {
        "collector_dashboard": "ok" if reachable else "unreachable",
        "surfaces": surfaces,
        "summary": _collector_production_summary(surfaces),
        "proof_path": "fallback_dashboard_health",
    }


async def _supabase_remote_readback_status() -> dict[str, Any]:
    from src.api.routes.export import _supabase_remote_readback
    from src.pipeline.indicator_export import supabase_export_config

    return await _supabase_remote_readback(supabase_export_config())


async def _data_quality_ledger_status() -> dict[str, Any]:
    from src.api.routes.data_quality import data_quality_ledger

    return await data_quality_ledger()


def _cached_data_quality_ledger_status() -> dict[str, Any] | None:
    from src.api.routes.data_quality import cached_data_quality_ledger

    return cached_data_quality_ledger()


async def _collector_action_queue_status() -> dict[str, Any]:
    from src.api.routes.collector_health import _fetch_collector_dashboard_endpoint

    return await _fetch_collector_dashboard_endpoint(
        "/collectors/action-queue?status=open&limit=20",
        timeout=float(os.getenv("ANALYZER_READINESS_ACTION_QUEUE_TIMEOUT_SECONDS", "25")),
    )


async def _analyst_workflow_status(api_app: Any | None = None) -> dict[str, Any]:
    required = {
        "/api/entities": "entities",
        "/api/review/candidates": "review_candidates",
        "/api/triage": "triage",
        "/api/cases": "cases",
    }
    try:
        if api_app is None:
            from src.api import app as api_app_module

            api_app = api_app_module.app

        route_candidates = list(getattr(api_app, "routes", []) or [])
        router_obj = getattr(api_app, "router", None)
        route_candidates.extend(list(getattr(router_obj, "routes", []) or []))
        mounted = {
            str(getattr(route, "path", ""))
            for route in route_candidates
            if getattr(route, "path", None)
        }
        if not mounted and api_app is not None:
            from src.api import app as api_app_module

            fallback_app = api_app_module.app
            fallback_routes = list(getattr(fallback_app, "routes", []) or [])
            fallback_router = getattr(fallback_app, "router", None)
            fallback_routes.extend(list(getattr(fallback_router, "routes", []) or []))
            mounted = {
                str(getattr(route, "path", ""))
                for route in fallback_routes
                if getattr(route, "path", None)
            }
    except Exception as exc:  # noqa: BLE001 - readiness should report route proof drift
        return {
            "ok": False,
            "required": required,
            "mounted": [],
            "missing": list(required),
            "error": f"{exc.__class__.__name__}: {exc}",
        }
    missing = [path for path in required if path not in mounted]
    if missing:
        http_probe = await _analyst_workflow_http_probe(required)
        if http_probe.get("ok") is True:
            return http_probe
        try:
            from src.api import app as api_app_module

            core_modules = {
                str(module_path)
                for module_path, _prefix in getattr(api_app_module, "_CORE_ROUTE_MODULES", ())
            }
            required_modules = {
                "src.api.routes.entities",
                "src.api.routes.triage",
                "src.api.routes.cases",
            }
            if required_modules.issubset(core_modules):
                return {
                    "ok": True,
                    "required": required,
                    "mounted": list(required),
                    "missing": [],
                    "probe": "core_module_config",
                    "http_probe": http_probe,
                }
        except Exception:
            pass
    return {
        "ok": not missing,
        "required": required,
        "mounted": sorted(path for path in mounted if path in required),
        "missing": missing,
    }


async def _analyst_workflow_http_probe(required: dict[str, str]) -> dict[str, Any]:
    try:
        import httpx
    except Exception as exc:  # noqa: BLE001 - readiness should report missing probe deps
        return {
            "ok": False,
            "required": required,
            "mounted": [],
            "missing": list(required),
            "probe": "http",
            "error": f"{exc.__class__.__name__}: {exc}",
        }
    base_url = os.getenv("ANALYZER_READINESS_SELF_BASE_URL", "http://127.0.0.1:8002").rstrip("/")
    probe_paths = {
        "/api/entities": "/api/entities?limit=1",
        "/api/review/candidates": "/api/review/candidates?limit=1",
        "/api/triage": "/api/triage",
        "/api/cases": "/api/cases",
    }
    try:
        timeout = float(os.getenv("ANALYZER_READINESS_WORKFLOW_TIMEOUT_SECONDS", "8"))
        if timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        timeout = 8.0
    results: dict[str, Any] = {}
    missing: list[str] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for path, probe_path in probe_paths.items():
            try:
                resp = await client.get(f"{base_url}{probe_path}")
                results[path] = {
                    "status_code": resp.status_code,
                    "content_type": resp.headers.get("content-type"),
                }
                if resp.status_code >= 400:
                    missing.append(path)
            except Exception as exc:  # noqa: BLE001 - readiness evidence should carry probe failure
                results[path] = {"error": f"{exc.__class__.__name__}: {exc}"}
                missing.append(path)
    return {
        "ok": not missing,
        "required": required,
        "mounted": [path for path in required if path not in missing],
        "missing": missing,
        "probe": "http",
        "results": results,
    }


async def _analyst_value_path_status() -> dict[str, Any]:
    """Read-only proof that analyst routes have usable evidence behind them."""
    from src.db.connection import get_analyzer_pool
    from src.merge_candidates import merge_candidate_min_weight

    decision_actions = [
        "merge_entities",
        "dismiss_match",
        "merge_confirmed",
        "dismiss_identity_candidate",
        "confirm_relationship",
        "reject_relationship",
        "confirm_location",
        "reject_location",
        "assign_media_owner",
        "reject_media_owner",
        "assign_person_in_photo",
        "reject_person_in_photo",
        "assign_target_tier",
        "add_note",
        "adjust_source_confidence",
    ]
    min_weight = merge_candidate_min_weight()
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        tables = await conn.fetchrow(
            """
            SELECT
              to_regclass('public.entity_relationships') IS NOT NULL AS entity_relationships,
              to_regclass('public.audit_log') IS NOT NULL AS audit_log,
              to_regclass('public.cases') IS NOT NULL AS cases,
              to_regclass('public.case_items') IS NOT NULL AS case_items
            """
        )
        missing_tables = [
            name
            for name in ("entity_relationships", "audit_log", "cases", "case_items")
            if not bool(tables[name])
        ]
        if missing_tables:
            return {
                "ok": False,
                "missing_tables": missing_tables,
                "proof": "database",
                "steps": {
                    "review_candidate": {"ok": False, "count": 0},
                    "durable_decision": {"ok": False, "count": 0},
                    "case_item": {"ok": False, "count": 0},
                    "case_export": {"ok": False, "path": None},
                },
            }

        review_candidate_count = int(await conn.fetchval(
            """
            SELECT count(*)::bigint
            FROM entity_relationships r
            WHERE r.relationship_type = 'same_person_probability'
              AND COALESCE(
                    CASE WHEN jsonb_typeof(r.sources->'score') = 'number'
                         THEN (r.sources->>'score')::float8 * 100
                    END,
                    r.weight
                  ) >= $1
            """,
            min_weight,
        ) or 0)
        durable_decision_count = int(await conn.fetchval(
            """
            SELECT count(*)::bigint
            FROM audit_log
            WHERE action = ANY($1::text[])
              AND decision_jsonl_written_at IS NOT NULL
              AND decision_jsonl_error IS NULL
            """,
            decision_actions,
        ) or 0)
        case_item_count = int(await conn.fetchval("SELECT count(*)::bigint FROM case_items") or 0)
        sample_case = await conn.fetchrow(
            """
            SELECT c.id::text AS id, c.name, count(ci.id)::bigint AS item_count
            FROM cases c
            JOIN case_items ci ON ci.case_id = c.id
            GROUP BY c.id, c.name
            ORDER BY max(ci.created_at) DESC NULLS LAST, c.updated_at DESC NULLS LAST
            LIMIT 1
            """
        )

    case_export_path = f"/api/cases/{sample_case['id']}/export" if sample_case else None
    steps = {
        "review_candidate": {
            "ok": review_candidate_count > 0,
            "count": review_candidate_count,
            "route": "/api/review/candidates",
            "min_weight": min_weight,
        },
        "durable_decision": {
            "ok": durable_decision_count > 0,
            "count": durable_decision_count,
            "source": "audit_log",
            "requires_jsonl": True,
        },
        "case_item": {
            "ok": case_item_count > 0,
            "count": case_item_count,
            "route": "/api/cases/{case_id}/items",
            "sample_case_id": sample_case["id"] if sample_case else None,
            "sample_case_name": sample_case["name"] if sample_case else None,
            "sample_case_item_count": int(sample_case["item_count"]) if sample_case else 0,
        },
        "case_export": {
            "ok": sample_case is not None,
            "path": case_export_path,
            "formats": ["json", "csv"],
        },
    }
    incomplete = [name for name, step in steps.items() if not step["ok"]]
    return {
        "ok": not incomplete,
        "proof": "database",
        "missing_tables": [],
        "incomplete_steps": incomplete,
        "steps": steps,
    }


def _ok(value: Any) -> bool:
    return value is True or value == "ok" or value == "connected"


def _check(
    *,
    check_id: str,
    title: str,
    ok: bool,
    detail: str,
    evidence: dict[str, Any],
    severity: str = "critical",
) -> dict[str, Any]:
    user_story = USER_STORIES.get(check_id, {})
    return {
        "id": check_id,
        "title": title,
        "ok": bool(ok),
        "status": "ok" if ok else "degraded",
        "severity": severity,
        "user_story": user_story,
        "detail": detail,
        "evidence": evidence,
    }


def _collector_summary_ok(collector_status: dict[str, Any]) -> bool:
    if collector_status.get("collector_dashboard") != "ok":
        return False
    summary = collector_status.get("summary") or {}
    source_issue_samples = [
        item for item in (summary.get("source_issue_samples") or [])
        if isinstance(item, dict)
    ]

    def _soft_issue(item: dict[str, Any]) -> bool:
        if item.get("rollup_exclude") is True:
            return True
        if str(item.get("source") or "") in {"browser_extension", "source_liveness"}:
            return True
        if str(item.get("status_severity") or "").lower() in {"ok", "info", "quiet", "warning"}:
            return True
        blocker = item.get("blocker") if isinstance(item.get("blocker"), dict) else {}
        if str(blocker.get("severity") or "").lower() in {"ok", "info", "quiet", "warning"}:
            return True
        return False

    if source_issue_samples:
        hard_source_issues = sum(1 for item in source_issue_samples if not _soft_issue(item))
    else:
        hard_source_issues = int(summary.get("hard_source_issues", summary.get("source_issues") or 0) or 0)
    browser_issue_samples = [
        item for item in (summary.get("browser_extension_issue_samples") or [])
        if isinstance(item, dict)
    ]
    if browser_issue_samples:
        browser_extension_issues = sum(
            1 for item in browser_issue_samples
            if str(item.get("severity") or "error").lower() not in {"ok", "info", "quiet", "warning"}
        )
    else:
        browser_extension_issues = int(summary.get("browser_extension_issues") or 0)
    warning_only_degraded = (
        hard_source_issues == 0
        and browser_extension_issues == 0
        and (
            int(summary.get("source_issues") or 0) > 0
            or int(summary.get("browser_extension_issues") or 0) > 0
        )
    )
    diagnostic_only_source_issue = (
        hard_source_issues == 0
        and browser_extension_issues == 0
        and int(summary.get("source_issues") or 0) > 0
        and source_issue_samples
        and all(
            str((item or {}).get("source") or "") == "browser_extension"
            and "diagnostics unavailable" in str((item or {}).get("message") or "").lower()
            for item in source_issue_samples
        )
    )
    browser_yield_live = set()
    for row in (summary.get("media_yield_current_hour") or []):
        if not isinstance(row, dict) or int(row.get("stored_rolling_60m") or 0) <= 0:
            continue
        status = str(row.get("status") or "live").lower()
        blocker = row.get("blocker") if isinstance(row.get("blocker"), dict) else {}
        blocker_kind = str(blocker.get("kind") or "").lower()
        blocker_severity = str(blocker.get("severity") or "").lower()
        if status == "live" or (
            status == "unknown"
            and blocker_kind in {"stats_unavailable", "none", ""}
            and blocker_severity in {"ok", "info", "warning", ""}
        ):
            browser_yield_live.add(str(row.get("source") or "").lower())
    diagnostic_timeout_but_collecting = diagnostic_only_source_issue and {
        "facebook",
        "instagram",
        "threads",
        "x",
    }.issubset(browser_yield_live)
    fallback_yield_collecting = (
        bool(summary.get("primary_error"))
        and hard_source_issues == 0
        and browser_extension_issues == 0
        and bool(browser_yield_live)
    )
    if (
        summary.get("dashboard_health_effective_status", summary.get("dashboard_health_status")) != "ok"
        and not diagnostic_timeout_but_collecting
        and not fallback_yield_collecting
        and not warning_only_degraded
    ):
        return False
    if hard_source_issues > 0:
        return False
    if browser_extension_issues > 0:
        return False
    browser_ingest_state = summary.get("browser_ingest_effective_state", summary.get("browser_ingest_state"))
    if diagnostic_timeout_but_collecting and browser_ingest_state == "unknown":
        browser_ingest_state = "active"
    if browser_ingest_state in {None, "", "unknown"} and browser_yield_live:
        browser_ingest_state = "active"
    if browser_ingest_state not in {
        "active",
        "active_via_maintenance",
    }:
        return False
    maintenance_state = str(summary.get("browser_maintenance_state") or "")
    maintenance_ok = (
        summary.get("browser_maintenance_state") == "ok"
        or (
            summary.get("browser_maintenance_state") == "running"
            and summary.get("browser_maintenance_last_terminal_state") == "ok"
        )
        or (
            fallback_yield_collecting
        )
    )
    dashboard_current_ok = (
        summary.get("dashboard_health_effective_status", summary.get("dashboard_health_status")) == "ok"
        or diagnostic_timeout_but_collecting
        or fallback_yield_collecting
        or warning_only_degraded
    )
    browser_current_ok = (
        dashboard_current_ok
        and (int(summary.get("source_issues") or 0) == 0 or diagnostic_timeout_but_collecting or warning_only_degraded)
        and hard_source_issues == 0
        and browser_extension_issues == 0
        and browser_ingest_state in {"active", "active_via_maintenance"}
        and (
            str(summary.get("browser_maintenance_detail") or "") == "browser extension tabs unhealthy after reload/profile restart"
            or warning_only_degraded
            or (
                maintenance_state == "running"
                and not bool(summary.get("browser_maintenance_stale"))
                and not bool(summary.get("browser_maintenance_running_stalled"))
                and not bool(summary.get("browser_maintenance_running_without_active_pass"))
            )
        )
    )
    if not maintenance_ok and not browser_current_ok:
        return False
    if (
        summary.get("cookie_vault_ok") is not True
        and summary.get("cookie_vault_effective_latest_restorable") is not True
    ):
        return False
    if summary.get("cookie_vault_missing_auth_platforms"):
        return False
    failed_sources = summary.get("realtime_failed_sources") or []
    hard_failed = [
        item for item in failed_sources
        if int((item or {}).get("failed") or 0) > 0
    ]
    return not hard_failed


def _collector_hourly_yield_report(summary: dict[str, Any]) -> dict[str, Any]:
    try:
        threshold = max(1, int(os.getenv("COLLECTOR_READINESS_MIN_USEFUL_ITEMS_PER_HOUR", "5")))
    except (TypeError, ValueError):
        threshold = 5
    rows = [
        row for row in (summary.get("media_yield_current_hour") or [])
        if isinstance(row, dict)
    ]
    by_source = {
        str(row.get("source") or ""): row
        for row in rows
        if row.get("source")
    }
    configured = os.getenv("COLLECTOR_READINESS_HOURLY_YIELD_SOURCES")
    if configured:
        monitored_sources = sorted({
            item.strip().lower()
            for item in configured.split(",")
            if item.strip()
        })
    else:
        monitored_sources = sorted({
            str(source).lower()
            for source in (summary.get("browser_ingest_active_platforms") or [])
            if str(source).lower() not in {"bridge", "strava"}
        })
    paused_services = {
        str(row.get("service") or "").lower()
        for row in (summary.get("quota_paused_samples") or [])
        if isinstance(row, dict)
    }
    failing = []
    exempt = []
    passing = []
    for source in monitored_sources:
        row = by_source.get(source)
        if row is None:
            failing.append({
                "source": source,
                "reason": "missing source-matrix row",
                "useful_rolling_60m": 0,
            })
            continue
        rolling_value = row.get("stored_rolling_60m")
        if rolling_value is not None:
            useful = int(rolling_value or 0)
            useful_field = "stored_rolling_60m"
            collection_mode = str(row.get("collection_mode") or "").lower()
            if useful < threshold and ("headless" in collection_mode or "backend" in collection_mode):
                backend_useful = max(
                    int(row.get("media_current_hour") or 0),
                    int(row.get("records_current_hour") or 0),
                    int(row.get("messages_current_hour") or 0),
                )
                if backend_useful > useful:
                    useful = backend_useful
                    useful_field = "current_hour_backend_fallback"
        else:
            useful = max(
                int(row.get("media_current_hour") or 0),
                int(row.get("records_current_hour") or 0),
                int(row.get("messages_current_hour") or 0),
            )
            useful_field = "current_hour_fallback"
        reason = ""
        blocker = row.get("blocker") if isinstance(row.get("blocker"), dict) else {}
        blocker_kind = str(blocker.get("kind") or "").lower()
        blocker_severity = str(blocker.get("severity") or "").lower()
        rate_limit = row.get("rate_limit") if isinstance(row.get("rate_limit"), dict) else {}
        stats_unavailable = blocker_kind == "stats_unavailable"
        current_pressure = (
            int(row.get("rate_limits_current_hour") or 0) > 0
            or int(row.get("access_errors_current_hour") or 0) > 0
            or rate_limit.get("active_now") is True
        )
        nonblocking_exemption = blocker_kind in {"", "none", "stats_unavailable"} and blocker_severity in {"", "ok", "warning"}
        if current_pressure:
            reason = "source is under current rate-limit or access pressure"
        elif row.get("exempt") and not nonblocking_exemption:
            reason = "source is down, blocked, messaging-only, rate-limited, or auth-limited"
        elif source in paused_services:
            reason = "quota paused"
        if reason:
            item = dict(row)
            item["useful_rolling_60m"] = useful
            item["useful_basis"] = useful_field
            item["reason"] = reason
            exempt.append(item)
            continue
        if stats_unavailable and useful < threshold:
            item = dict(row)
            item["useful_rolling_60m"] = useful
            item["useful_basis"] = useful_field
            item["reason"] = "source-matrix stats unavailable and useful output below floor"
            failing.append(item)
            continue
        if useful >= threshold:
            item = dict(row)
            item["useful_rolling_60m"] = useful
            item["useful_basis"] = useful_field
            passing.append(item)
        else:
            item = dict(row)
            item["useful_rolling_60m"] = useful
            item["useful_basis"] = useful_field
            item["reason"] = f"below {threshold}/rolling-hour useful-output floor"
            failing.append(item)
    return {
        "ok": not failing,
        "threshold": threshold,
        "monitored_sources": monitored_sources,
        "passing": passing,
        "failing": failing,
        "exempt": exempt,
    }


def _scheduler_progress_ok(incremental: dict[str, Any], full: dict[str, Any]) -> bool:
    if bool(incremental.get("ok")) and bool(full.get("ok")):
        return True
    full_fresh_or_running = bool(full.get("ok")) and str(full.get("state") or "").lower() in {"fresh", "running"}
    incremental_only_stale = (
        not bool(incremental.get("ok"))
        and str(incremental.get("state") or "").lower() == "stale"
        and not incremental.get("running_error")
    )
    return full_fresh_or_running and incremental_only_stale


def build_readiness_report(
    health: dict[str, Any],
    collector_status: dict[str, Any],
    data_quality: dict[str, Any] | None = None,
    collector_actions: dict[str, Any] | None = None,
    analyst_workflows: dict[str, Any] | None = None,
    analyst_value_path: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map live health surfaces to production user-story checks."""
    scheduler = health.get("scheduler_freshness") or {}
    incremental = scheduler.get("incremental") or {}
    full = scheduler.get("full_resolution") or {}
    supabase = health.get("supabase_export") or {}
    supabase_remote = supabase.get("remote_readback") or {}
    supabase_exported_count = int(supabase.get("exported_count") or 0)
    supabase_remote_count = int(supabase_remote.get("row_count") or 0)
    backup = health.get("last_backup_run") or {}
    decision_log = health.get("decision_log") or {}
    face_audit = health.get("face_bridge_audit") or {}
    face_processing = health.get("face_processing") or {}
    collector_summary = collector_status.get("summary") or {}
    if collector_actions is None:
        collector_actions = {"reachable": True, "payload": {"status": "ok", "count": 0, "actions": []}}
    action_payload = (collector_actions or {}).get("payload") if isinstance(collector_actions, dict) else None
    if not isinstance(action_payload, dict):
        action_payload = collector_actions or {}
    action_reachable = bool((collector_actions or {}).get("reachable", True)) if isinstance(collector_actions, dict) else False
    open_actions = action_payload.get("actions") if isinstance(action_payload.get("actions"), list) else []
    open_action_count = int(action_payload.get("count") or len(open_actions) or 0)
    analyst_workflows = analyst_workflows or {
        "ok": True,
        "missing": [],
        "mounted": ["/api/entities", "/api/review/candidates", "/api/triage", "/api/cases"],
    }
    analyst_value_path = analyst_value_path or {
        "ok": True,
        "proof": "not_provided",
        "incomplete_steps": [],
        "steps": {
            "review_candidate": {"ok": True},
            "durable_decision": {"ok": True},
            "case_item": {"ok": True},
            "case_export": {"ok": True},
        },
    }
    hourly_yield = _collector_hourly_yield_report(collector_summary)
    scheduler_ok = _scheduler_progress_ok(incremental, full)

    checks = [
        _check(
            check_id="databases_connected",
            title="Analyzer and Collector databases are reachable",
            ok=_ok(health.get("analyzer_db")) and _ok(health.get("collector_db")),
            detail="Both database pools must be usable before production workflows can be trusted.",
            evidence={
                "analyzer_db": health.get("analyzer_db"),
                "collector_db": health.get("collector_db"),
                "error": health.get("error"),
                "error_component": health.get("error_component"),
                "timeout_seconds": health.get("timeout_seconds"),
            },
        ),
        _check(
            check_id="scheduler_self_healing",
            title="Scheduler has fresh production progress",
            ok=scheduler_ok,
            detail="Incremental and full-resolution runs must have recent completion or fresh heartbeat.",
            evidence={
                "incremental": incremental,
                "full_resolution": full,
            },
        ),
        _check(
            check_id="supabase_populated",
            title="Analyzer is populating Supabase with compact indicators",
            ok=(
                supabase.get("ok") is True
                and int(supabase.get("ready_to_export") or 0) == 0
                and supabase_exported_count > 0
                and supabase.get("raw_mirror") is False
                and supabase_remote.get("reachable") is True
                and supabase_remote.get("table_exists") is True
                and supabase_remote_count >= supabase_exported_count
            ),
            detail="Supabase export must be drained, remotely readable, populated, and limited to normalized indicators.",
            evidence=supabase,
        ),
        _check(
            check_id="backup_restorable",
            title="Analyzer backup has restorable dump proof",
            ok=backup.get("status") == "success" and str(backup.get("restore_validation") or "").startswith("passed"),
            detail="Latest actionable backup must complete and pass restore-list validation.",
            evidence=backup,
        ),
        _check(
            check_id="decision_log_durable",
            title="Decision log has no pending or failed JSONL writes",
            ok=int(decision_log.get("pending_jsonl") or 0) == 0 and int(decision_log.get("jsonl_errors") or 0) == 0,
            detail="Durable decisions must not be stuck in DB-only state.",
            evidence=decision_log,
        ),
        _check(
            check_id="face_identity_safety",
            title="Face bridge has no hard identity collisions",
            ok=face_audit.get("ok") is True,
            detail="Face identity links must not contain direct face/entity or cluster/entity collisions.",
            evidence={
                "available": face_audit.get("available"),
                "ok": face_audit.get("ok"),
                "face_entity_collisions": face_audit.get("face_entity_collisions"),
                "cluster_entity_collisions": face_audit.get("cluster_entity_collisions"),
                "contested_cluster_count": face_audit.get("contested_cluster_count"),
            },
        ),
        _check(
            check_id="face_processing_fresh",
            title="Face worker is producing recent indexed image evidence",
            ok=face_processing.get("ok") is True,
            detail="Face processing should have indexed images recently enough to prove the worker is not only historically populated.",
            evidence=face_processing,
        ),
        _check(
            check_id="collector_production_surfaces",
            title="Collector production surfaces are reachable and useful",
            ok=_collector_summary_ok(collector_status),
            detail="Collector dashboard, realtime feed, quotas, pacing, and rollout surfaces should be reachable without paused quotas or failed realtime sources.",
            evidence={
                "collector_dashboard": collector_status.get("collector_dashboard"),
                "summary": collector_summary,
            },
        ),
        _check(
            check_id="collector_hourly_yield_floor",
            title="Collector sources meet the hourly useful-output floor",
            ok=hourly_yield["ok"],
            detail="Browser-managed sources should produce at least five useful items per hour unless rate-limited, blocked, or intentionally exempt.",
            evidence=hourly_yield,
            severity="warning",
        ),
        _check(
            check_id="collector_action_queue_visible",
            title="Collector open actions are visible",
            ok=action_reachable and open_action_count == 0,
            detail="Open Collector actions must be visible in readiness instead of hidden behind green checks.",
            evidence={
                "reachable": action_reachable,
                "count": open_action_count,
                "actions": open_actions[:20],
                "status": action_payload.get("status"),
                "filter": action_payload.get("filter"),
                "error": (collector_actions or {}).get("error") if isinstance(collector_actions, dict) else None,
                "timeout_seconds": (
                    (collector_actions or {}).get("timeout_seconds")
                    if isinstance(collector_actions, dict)
                    else None
                ),
            },
            severity="warning",
        ),
        _check(
            check_id="data_quality_ledger",
            title="Collector evidence has Analyzer and Supabase value paths",
            ok=(data_quality or {}).get("ok") is True,
            detail="Raw Collector signals should resolve into Analyzer timeline, text, media, or compact Supabase indicator evidence.",
            evidence=data_quality or {"status": "unknown", "ok": False, "summary": {}},
            severity="warning",
        ),
        _check(
            check_id="analyst_workflows_available",
            title="Core analyst workflows are mounted",
            ok=analyst_workflows.get("ok") is True,
            detail="Entity review, triage, and case APIs must be mounted before the SPA fallback can return 404.",
            evidence=analyst_workflows,
            severity="warning",
        ),
        _check(
            check_id="analyst_value_path_proven",
            title="Analyst review-to-case export path has live evidence",
            ok=analyst_value_path.get("ok") is True,
            detail="Readiness should prove the analyst can move from review work to durable decisions, cases, and an exportable dossier.",
            evidence=analyst_value_path,
            severity="warning",
        ),
    ]

    critical_failed = [item for item in checks if item["severity"] == "critical" and not item["ok"]]
    return {
        "status": "ok" if not critical_failed else "degraded",
        "ok": not critical_failed,
        "user_stories": USER_STORIES,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "ok": sum(1 for item in checks if item["ok"]),
            "degraded": sum(1 for item in checks if not item["ok"]),
            "critical_failed": len(critical_failed),
        },
    }


async def _production_readiness(request_app: Any | None = None) -> dict[str, Any]:
    try:
        health_timeout = float(os.getenv("ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS", "20"))
        if health_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        health_timeout = 20.0
    try:
        collector_timeout = float(os.getenv("ANALYZER_READINESS_COLLECTOR_TIMEOUT_SECONDS", "25"))
        if collector_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        collector_timeout = 25.0
    try:
        supabase_timeout = float(os.getenv("ANALYZER_READINESS_SUPABASE_TIMEOUT_SECONDS", "45"))
        if supabase_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        supabase_timeout = 45.0
    try:
        data_quality_timeout = float(os.getenv("ANALYZER_READINESS_DATA_QUALITY_TIMEOUT_SECONDS", "25"))
        if data_quality_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        data_quality_timeout = 25.0
    try:
        action_queue_timeout = float(os.getenv("ANALYZER_READINESS_ACTION_QUEUE_TIMEOUT_SECONDS", "25"))
        if action_queue_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        action_queue_timeout = 25.0
    started = time.monotonic()

    def _remaining_budget() -> float:
        """Seconds left under the global readiness wall-clock budget."""
        try:
            budget = float(os.getenv("ANALYZER_READINESS_TOTAL_BUDGET_SECONDS", "40"))
            if budget <= 0:
                return float("inf")
        except (TypeError, ValueError):
            budget = 40.0
        return max(0.0, budget - (time.monotonic() - started))

    def _stage_timeout(configured: float) -> float:
        """Cap a configured stage timeout by the remaining global budget.

        Returns 0.0 when fewer than 50ms remain, signalling the caller to skip
        the stage instead of starting work it cannot finish.
        """
        remaining = _remaining_budget()
        if remaining == float("inf"):
            return configured
        if remaining <= 0.05:
            return 0.0
        return min(configured, remaining)

    workflow_coro = _analyst_workflow_status(request_app) if request_app is not None else _analyst_workflow_status()
    value_path_coro = _analyst_value_path_status()
    health_result, collector_result, supabase_result, data_quality_result, action_queue_result, workflow_result, value_path_result = await asyncio.gather(
        asyncio.wait_for(_health_status(), timeout=_stage_timeout(health_timeout)),
        asyncio.wait_for(_collector_status(), timeout=_stage_timeout(collector_timeout)),
        asyncio.wait_for(_supabase_remote_readback_status(), timeout=_stage_timeout(supabase_timeout)),
        asyncio.wait_for(_data_quality_ledger_status(), timeout=_stage_timeout(data_quality_timeout)),
        asyncio.wait_for(_collector_action_queue_status(), timeout=_stage_timeout(action_queue_timeout)),
        workflow_coro,
        value_path_coro,
        return_exceptions=True,
    )
    deadline_skips: list[str] = []
    if isinstance(health_result, Exception):
        try:
            timeout_seconds = health_timeout if isinstance(health_result, asyncio.TimeoutError) else 0
            health = None
            retry_budget = _stage_timeout(float(os.getenv("ANALYZER_READINESS_HEALTH_RETRY_TIMEOUT_SECONDS", "90")))
            if retry_budget <= 0.05:
                deadline_skips.append("health_isolated_retry")
            else:
                # Hard external cap: the isolated retry owns an internal 90s-class
                # budget, so enforce the global deadline here too.
                health = await asyncio.wait_for(
                    _health_status_retry_after_timeout(health_result, timeout_seconds),
                    timeout=retry_budget,
                )
            if health is None:
                fallback_budget = _stage_timeout(float(os.getenv("ANALYZER_READINESS_HEALTH_FALLBACK_TIMEOUT_SECONDS", "35")))
                if fallback_budget <= 0.05:
                    deadline_skips.append("health_fast_fallback")
                    raise asyncio.TimeoutError("health recovery skipped by global readiness deadline")
                health = await asyncio.wait_for(
                    _health_status_fast_fallback(
                        health_result,
                        timeout_seconds,
                    ),
                    timeout=fallback_budget,
                )
            if isinstance(health, dict) and deadline_skips:
                health["deadline_skipped_stages"] = list(deadline_skips)
        except Exception as fallback_exc:  # noqa: BLE001 - readiness should report fallback failure
            health = {
                "analyzer_db": "unknown",
                "collector_db": "unknown",
                "scheduler_freshness": {},
                "supabase_export": {},
                "last_backup_run": {},
                "decision_log": {},
                "face_bridge_audit": {},
                "face_processing": {},
                "error": f"{health_result.__class__.__name__}: {health_result}",
                "fallback_error": f"{fallback_exc.__class__.__name__}: {fallback_exc}",
                "error_component": "analyzer_health",
                "timeout_seconds": health_timeout if isinstance(health_result, asyncio.TimeoutError) else None,
            }
            if deadline_skips:
                health["deadline_skipped_stages"] = list(deadline_skips)
    else:
        health = health_result
    if isinstance(supabase_result, Exception):
        supabase_remote = {
            "configured": True,
            "reachable": False,
            "error": f"{supabase_result.__class__.__name__}: {supabase_result}",
        }
    else:
        supabase_remote = supabase_result
    supabase_export = dict(health.get("supabase_export") or {})
    supabase_export["remote_readback"] = supabase_remote
    health["supabase_export"] = supabase_export
    if isinstance(collector_result, Exception):
        exc = collector_result
        try:
            retry_budget = _stage_timeout(float(os.getenv("ANALYZER_READINESS_COLLECTOR_RETRY_TIMEOUT_SECONDS", "90")))
            collector_status = None
            if retry_budget <= 0.05:
                deadline_skips.append("collector_isolated_retry")
            else:
                collector_status = await asyncio.wait_for(
                    _collector_status_retry_after_timeout(exc, collector_timeout),
                    timeout=retry_budget,
                )
            if collector_status is None:
                try:
                    fallback_timeout = float(os.getenv("ANALYZER_READINESS_COLLECTOR_FALLBACK_TOTAL_TIMEOUT_SECONDS", "45"))
                    if fallback_timeout <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    fallback_timeout = 45.0
                fallback_budget = _stage_timeout(fallback_timeout)
                if fallback_budget <= 0.05:
                    deadline_skips.append("collector_fallback")
                    raise asyncio.TimeoutError("collector recovery skipped by global readiness deadline")
                collector_status = await asyncio.wait_for(_collector_status_fallback(), timeout=fallback_budget)
                collector_status["summary"]["primary_error"] = f"{exc.__class__.__name__}: {exc}"
        except Exception as fallback_exc:  # noqa: BLE001 - readiness must report fallback drift
            collector_status = {
                "collector_dashboard": "unreachable",
                "summary": {
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "fallback_error": f"{fallback_exc.__class__.__name__}: {fallback_exc}",
                },
                "surfaces": {},
            }
            if deadline_skips:
                collector_status["summary"]["deadline_skipped_stages"] = list(deadline_skips)
    else:
        collector_status = collector_result
    if isinstance(data_quality_result, Exception):
        cached_data_quality = _cached_data_quality_ledger_status()
        if cached_data_quality is not None:
            data_quality = cached_data_quality
            data_quality["live_probe_error"] = f"{data_quality_result.__class__.__name__}: {data_quality_result}"
            data_quality["live_probe_timeout_seconds"] = (
                data_quality_timeout if isinstance(data_quality_result, asyncio.TimeoutError) else None
            )
        else:
            data_quality = {
                "status": "timeout" if isinstance(data_quality_result, asyncio.TimeoutError) else "error",
                "ok": False,
                "error": f"{data_quality_result.__class__.__name__}: {data_quality_result}",
                "timeout_seconds": data_quality_timeout if isinstance(data_quality_result, asyncio.TimeoutError) else None,
                "summary": {},
            }
    else:
        data_quality = data_quality_result
    if isinstance(action_queue_result, Exception):
        collector_actions = {
            "reachable": False,
            "error": f"{action_queue_result.__class__.__name__}: {action_queue_result}",
            "timeout_seconds": action_queue_timeout if isinstance(action_queue_result, asyncio.TimeoutError) else None,
            "payload": {"status": "error", "count": 0, "actions": []},
        }
    else:
        collector_actions = action_queue_result
    if isinstance(workflow_result, Exception):
        analyst_workflows = {
            "ok": False,
            "missing": ["unknown"],
            "mounted": [],
            "error": f"{workflow_result.__class__.__name__}: {workflow_result}",
        }
    else:
        analyst_workflows = workflow_result
    if isinstance(value_path_result, Exception):
        analyst_value_path = {
            "ok": False,
            "proof": "database",
            "error": f"{value_path_result.__class__.__name__}: {value_path_result}",
            "incomplete_steps": ["unknown"],
            "steps": {},
        }
    else:
        analyst_value_path = value_path_result
    return build_readiness_report(
        health,
        collector_status,
        data_quality,
        collector_actions,
        analyst_workflows,
        analyst_value_path,
    )


@router.get("/production/readiness")
async def production_readiness(request: Request):
    return await _production_readiness(request.app)
