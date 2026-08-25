import asyncio

import pytest

from src.api.routes import readiness


def _healthy_data_quality():
    return {
        "status": "ok",
        "ok": True,
        "summary": {"total_sources": 2, "ok_sources": 2, "gap_sources": 0},
        "sources": [
            {"source": "facebook", "state": "ok", "ok": True},
            {"source": "exposure", "state": "ok", "ok": True},
        ],
    }


@pytest.fixture(autouse=True)
def _stub_data_quality(monkeypatch):
    async def fake_data_quality():
        return _healthy_data_quality()

    monkeypatch.setattr(readiness, "_data_quality_ledger_status", fake_data_quality)


@pytest.fixture(autouse=True)
def _stub_collector_action_queue(monkeypatch):
    async def fake_action_queue():
        return {"reachable": True, "payload": {"status": "ok", "count": 0, "actions": []}}

    monkeypatch.setattr(readiness, "_collector_action_queue_status", fake_action_queue)


@pytest.fixture(autouse=True)
def _stub_analyst_workflows(monkeypatch):
    async def fake_workflows():
        return {
            "ok": True,
            "missing": [],
            "mounted": ["/api/entities", "/api/review/candidates", "/api/triage", "/api/cases"],
        }

    monkeypatch.setattr(readiness, "_analyst_workflow_status", fake_workflows)


@pytest.fixture(autouse=True)
def _stub_analyst_value_path(monkeypatch):
    async def fake_value_path():
        return {
            "ok": True,
            "proof": "database",
            "incomplete_steps": [],
            "steps": {
                "review_candidate": {"ok": True, "count": 2, "route": "/api/review/candidates"},
                "durable_decision": {"ok": True, "count": 3, "source": "audit_log"},
                "case_item": {
                    "ok": True,
                    "count": 1,
                    "route": "/api/cases/{case_id}/items",
                    "sample_case_id": "11111111-1111-1111-1111-111111111111",
                },
                "case_export": {
                    "ok": True,
                    "path": "/api/cases/11111111-1111-1111-1111-111111111111/export",
                    "formats": ["json", "csv"],
                },
            },
        }

    monkeypatch.setattr(readiness, "_analyst_value_path_status", fake_value_path)


def _healthy_health():
    return {
        "analyzer_db": "connected",
        "collector_db": "connected",
        "scheduler_freshness": {
            "incremental": {"ok": True, "state": "running"},
            "full_resolution": {"ok": True, "state": "fresh"},
        },
        "supabase_export": {
            "ok": True,
            "state": "ok",
            "ready_to_export": 0,
            "exported_count": 2368,
            "raw_mirror": False,
            "remote_readback": {
                "configured": True,
                "reachable": True,
                "table_exists": True,
                "row_count": 2368,
            },
        },
        "last_backup_run": {
            "status": "success",
            "restore_validation": "passed: pg_restore --list",
        },
        "decision_log": {
            "pending_jsonl": 0,
            "jsonl_errors": 0,
        },
        "face_bridge_audit": {
            "available": True,
            "ok": True,
            "face_entity_collisions": 0,
            "cluster_entity_collisions": 0,
            "contested_cluster_count": 18,
        },
        "face_processing": {
            "available": True,
            "ok": True,
            "state": "ok",
            "image_count": 142714,
            "face_count": 20602,
            "entity_face_count": 1207,
            "latest_image_age_seconds": 60,
        },
    }


def _healthy_collector():
    return {
        "collector_dashboard": "ok",
        "summary": {
            "dashboard_health_status": "ok",
            "dashboard_health_effective_status": "ok",
            "source_issues": 0,
            "hard_source_issues": 0,
            "browser_extension_issues": 0,
            "browser_ingest_state": "active",
            "browser_ingest_effective_state": "active",
            "browser_maintenance_state": "ok",
            "browser_maintenance_last_terminal_state": "ok",
            "cookie_vault_ok": True,
            "cookie_vault_missing_auth_platforms": [],
            "browser_ingest_active_platforms": ["facebook", "instagram", "threads", "x", "strava"],
            "media_yield_current_hour": [
                {"source": "facebook", "media_current_hour": 0, "records_current_hour": 0, "stored_rolling_60m": 5},
                {"source": "instagram", "media_current_hour": 0, "records_current_hour": 0, "stored_rolling_60m": 6},
                {"source": "threads", "media_current_hour": 0, "records_current_hour": 0, "stored_rolling_60m": 7},
                {"source": "x", "media_current_hour": 0, "records_current_hour": 0, "stored_rolling_60m": 5},
                {"source": "strava", "media_current_hour": 0, "records_current_hour": 0, "stored_rolling_60m": 0},
            ],
            "quota_paused": 0,
            "realtime_failed_sources": [
                {"source": "telegram", "failed": 0, "too_large": 2, "local_fallback": 1}
            ],
        },
    }


def test_build_readiness_report_passes_all_user_story_checks():
    report = readiness.build_readiness_report(_healthy_health(), _healthy_collector(), _healthy_data_quality())

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["summary"]["degraded"] == 0
    assert {item["id"] for item in report["checks"]} == {
        "databases_connected",
        "scheduler_self_healing",
        "supabase_populated",
        "backup_restorable",
        "decision_log_durable",
        "face_identity_safety",
        "face_processing_fresh",
        "collector_production_surfaces",
        "collector_hourly_yield_floor",
        "collector_action_queue_visible",
        "data_quality_ledger",
        "analyst_workflows_available",
        "analyst_value_path_proven",
    }
    assert set(report["user_stories"]) == {item["id"] for item in report["checks"]}
    for item in report["checks"]:
        story = item["user_story"]
        assert story["actor"] in {"operator", "analyst"}
        assert story["story"]
        assert story["value"]
        assert story["proves"]


def test_readiness_user_story_metadata_names_supabase_privacy_path():
    report = readiness.build_readiness_report(_healthy_health(), _healthy_collector(), _healthy_data_quality())
    supabase = next(item for item in report["checks"] if item["id"] == "supabase_populated")

    assert "raw Collector mirror" in supabase["user_story"]["story"]
    assert "privacy-bounded" in supabase["user_story"]["value"]
    assert "raw_mirror is false" in supabase["user_story"]["proves"]


def test_build_readiness_report_surfaces_data_quality_warning_without_failing_critical_status():
    data_quality = {
        "status": "degraded",
        "ok": False,
        "summary": {"total_sources": 1, "ok_sources": 0, "gap_sources": 1},
        "sources": [{"source": "website", "state": "gap", "ok": False}],
    }

    report = readiness.build_readiness_report(_healthy_health(), _healthy_collector(), data_quality)
    ledger = next(item for item in report["checks"] if item["id"] == "data_quality_ledger")

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert ledger["ok"] is False
    assert ledger["severity"] == "warning"
    assert ledger["evidence"]["summary"]["gap_sources"] == 1


def test_build_readiness_report_surfaces_open_collector_actions_as_warning():
    collector_actions = {
        "reachable": True,
        "payload": {
            "status": "ok",
            "count": 1,
            "actions": [
                {
                    "source": "lemon8",
                    "action_type": "source_blocked",
                    "reason": "browser capture warning",
                }
            ],
        },
    }

    report = readiness.build_readiness_report(
        _healthy_health(),
        _healthy_collector(),
        _healthy_data_quality(),
        collector_actions,
        {"ok": True, "missing": [], "mounted": ["/api/entities", "/api/review/candidates", "/api/triage", "/api/cases"]},
    )
    queue = next(item for item in report["checks"] if item["id"] == "collector_action_queue_visible")

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert queue["ok"] is False
    assert queue["severity"] == "warning"
    assert queue["evidence"]["count"] == 1
    assert queue["evidence"]["actions"][0]["source"] == "lemon8"


def test_build_readiness_report_surfaces_missing_analyst_workflows_as_warning():
    report = readiness.build_readiness_report(
        _healthy_health(),
        _healthy_collector(),
        _healthy_data_quality(),
        {"reachable": True, "payload": {"status": "ok", "count": 0, "actions": []}},
        {"ok": False, "missing": ["/api/review/candidates"], "mounted": ["/api/entities"]},
    )
    workflows = next(item for item in report["checks"] if item["id"] == "analyst_workflows_available")

    assert report["ok"] is True
    assert workflows["ok"] is False
    assert workflows["severity"] == "warning"
    assert workflows["evidence"]["missing"] == ["/api/review/candidates"]


def test_build_readiness_report_surfaces_incomplete_analyst_value_path_as_warning():
    value_path = {
        "ok": False,
        "proof": "database",
        "incomplete_steps": ["durable_decision", "case_item", "case_export"],
        "steps": {
            "review_candidate": {"ok": True, "count": 5, "route": "/api/review/candidates"},
            "durable_decision": {"ok": False, "count": 0, "source": "audit_log"},
            "case_item": {"ok": False, "count": 0, "route": "/api/cases/{case_id}/items"},
            "case_export": {"ok": False, "path": None, "formats": ["json", "csv"]},
        },
    }

    report = readiness.build_readiness_report(
        _healthy_health(),
        _healthy_collector(),
        _healthy_data_quality(),
        {"reachable": True, "payload": {"status": "ok", "count": 0, "actions": []}},
        {"ok": True, "missing": [], "mounted": ["/api/entities", "/api/review/candidates", "/api/triage", "/api/cases"]},
        value_path,
    )
    check = next(item for item in report["checks"] if item["id"] == "analyst_value_path_proven")

    assert report["ok"] is True
    assert check["ok"] is False
    assert check["severity"] == "warning"
    assert check["evidence"]["incomplete_steps"] == ["durable_decision", "case_item", "case_export"]
    assert "export a dossier" in check["user_story"]["story"]


def test_core_analyst_routes_are_mounted_before_spa_fallback():
    from src.api import app as api_app

    route_paths = [str(getattr(route, "path", "")) for route in api_app.app.routes]
    required = {"/api/entities", "/api/review/candidates", "/api/triage", "/api/cases"}

    assert required.issubset(set(route_paths))
    fallback_index = route_paths.index("/{full_path:path}")
    for path in required:
        assert route_paths.index(path) < fallback_index


def test_build_readiness_report_accepts_fresh_full_run_when_incremental_completion_is_stale():
    health = _healthy_health()
    health["scheduler_freshness"] = {
        "incremental": {
            "ok": False,
            "state": "stale",
            "detail": "latest completed run is just over threshold",
            "running_error": None,
        },
        "full_resolution": {
            "ok": True,
            "state": "running",
            "detail": "running run heartbeat is fresh",
            "running_heartbeat_age_seconds": 277,
        },
    }

    report = readiness.build_readiness_report(health, _healthy_collector(), _healthy_data_quality())
    scheduler = next(item for item in report["checks"] if item["id"] == "scheduler_self_healing")

    assert scheduler["ok"] is True
    assert report["ok"] is True


@pytest.mark.asyncio
async def test_production_readiness_uses_25s_data_quality_default_timeout(monkeypatch):
    seen: list[float] = []

    async def fake_health():
        return _healthy_health()

    async def fake_collector():
        return _healthy_collector()

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def fake_data_quality():
        return _healthy_data_quality()

    async def fake_wait_for(coro, timeout):
        seen.append(timeout)
        return await coro

    monkeypatch.delenv("ANALYZER_READINESS_DATA_QUALITY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", fake_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)
    monkeypatch.setattr(readiness, "_data_quality_ledger_status", fake_data_quality)
    monkeypatch.setattr(readiness.asyncio, "wait_for", fake_wait_for)

    report = await readiness._production_readiness()

    assert report["ok"] is True
    assert 25.0 in seen


@pytest.mark.asyncio
async def test_production_readiness_bounds_slow_health_status(monkeypatch):
    async def slow_health():
        await asyncio.sleep(999)

    async def fake_collector():
        return _healthy_collector()

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def fail_health_fallback(original_error, timeout_seconds):
        raise RuntimeError("fallback unavailable")

    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_RETRY_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(readiness, "_health_status", slow_health)
    monkeypatch.setattr(readiness, "_health_status_fast_fallback", fail_health_fallback)
    monkeypatch.setattr(readiness, "_collector_status", fake_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)

    report = await readiness._production_readiness()

    databases = next(item for item in report["checks"] if item["id"] == "databases_connected")
    assert report["ok"] is False
    assert databases["ok"] is False
    assert databases["evidence"]["error_component"] == "analyzer_health"
    assert databases["evidence"]["timeout_seconds"] == 0.01

@pytest.mark.asyncio
async def test_production_readiness_respects_global_deadline_when_health_recovery_stalls(monkeypatch):
    """Total readiness wall time stays bounded even when recovery chains stall.

    The isolated health retry can default to a 90s budget and the fast fallback
    another 35s; sequential recovery after a slow primary probe previously let
    the route exceed any client timeout. A global deadline must cap every stage,
    including recovery, and yield an honest degraded report instead of a hang.
    """
    async def slow_health():
        await asyncio.sleep(999)

    async def fake_collector():
        return _healthy_collector()

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def stalled_retry(original_error, timeout_seconds):
        await asyncio.sleep(30)
        return None

    async def deadline_exhausted_fallback(original_error, timeout_seconds):
        base = _healthy_health()
        base["analyzer_db"] = "unknown"
        base["collector_db"] = "unknown"
        base["fallback"] = "fast_health"
        return base

    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("ANALYZER_READINESS_TOTAL_BUDGET_SECONDS", "1.5")
    monkeypatch.setattr(readiness, "_health_status", slow_health)
    monkeypatch.setattr(readiness, "_health_status_retry_after_timeout", stalled_retry)
    monkeypatch.setattr(readiness, "_health_status_fast_fallback", deadline_exhausted_fallback)
    monkeypatch.setattr(readiness, "_collector_status", fake_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)

    loop = asyncio.get_running_loop()
    started = loop.time()
    report = await readiness._production_readiness()
    elapsed = loop.time() - started

    assert elapsed < 8.0, f"readiness took {elapsed:.2f}s, global deadline not enforced"
    databases = next(item for item in report["checks"] if item["id"] == "databases_connected")
    assert databases["ok"] is False
    assert report["status"] == "degraded"

@pytest.mark.asyncio
async def test_production_readiness_uses_isolated_health_retry_after_primary_timeout(monkeypatch):
    calls = 0

    async def flaky_health():
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(999)
        return _healthy_health()

    async def fake_collector():
        return _healthy_collector()

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def fail_health_fallback(original_error, timeout_seconds):
        raise AssertionError("fast DB fallback should not run when isolated retry succeeds")

    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_RETRY_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(readiness, "_health_status", flaky_health)
    monkeypatch.setattr(readiness, "_health_status_fast_fallback", fail_health_fallback)
    monkeypatch.setattr(readiness, "_collector_status", fake_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)

    report = await readiness._production_readiness()

    databases = next(item for item in report["checks"] if item["id"] == "databases_connected")
    assert report["ok"] is True
    assert databases["ok"] is True
    assert databases["evidence"]["timeout_seconds"] == 0.01


@pytest.mark.asyncio
async def test_production_readiness_uses_fast_health_fallback_after_primary_timeout(monkeypatch):
    async def slow_health():
        await asyncio.sleep(999)

    async def fake_collector():
        return _healthy_collector()

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def healthy_health_fallback(original_error, timeout_seconds):
        health = _healthy_health()
        health["fallback"] = "fast_health"
        health["primary_error"] = f"{original_error.__class__.__name__}: {original_error}"
        health["timeout_seconds"] = timeout_seconds
        return health

    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_RETRY_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(readiness, "_health_status", slow_health)
    monkeypatch.setattr(readiness, "_health_status_fast_fallback", healthy_health_fallback)
    monkeypatch.setattr(readiness, "_collector_status", fake_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)

    report = await readiness._production_readiness()

    databases = next(item for item in report["checks"] if item["id"] == "databases_connected")
    supabase = next(item for item in report["checks"] if item["id"] == "supabase_populated")
    assert report["ok"] is True
    assert databases["ok"] is True
    assert databases["evidence"]["timeout_seconds"] == 0.01
    assert supabase["ok"] is True


@pytest.mark.asyncio
async def test_production_readiness_bounds_slow_data_quality_as_warning(monkeypatch):
    async def fake_health():
        return _healthy_health()

    async def fake_collector():
        return _healthy_collector()

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def slow_data_quality():
        await asyncio.sleep(999)

    monkeypatch.setenv("ANALYZER_READINESS_DATA_QUALITY_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", fake_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)
    monkeypatch.setattr(readiness, "_data_quality_ledger_status", slow_data_quality)

    report = await readiness._production_readiness()

    ledger = next(item for item in report["checks"] if item["id"] == "data_quality_ledger")
    assert report["status"] == "ok"
    assert ledger["ok"] is False
    assert ledger["evidence"]["status"] == "timeout"
    assert ledger["evidence"]["timeout_seconds"] == 0.01


@pytest.mark.asyncio
async def test_production_readiness_uses_cached_data_quality_after_timeout(monkeypatch):
    async def fake_health():
        return _healthy_health()

    async def fake_collector():
        return _healthy_collector()

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def slow_data_quality():
        await asyncio.sleep(999)

    def cached_data_quality():
        payload = _healthy_data_quality()
        payload["cache"] = {"used": True, "age_seconds": 12, "ttl_seconds": 900}
        return payload

    monkeypatch.setenv("ANALYZER_READINESS_DATA_QUALITY_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", fake_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)
    monkeypatch.setattr(readiness, "_data_quality_ledger_status", slow_data_quality)
    monkeypatch.setattr(readiness, "_cached_data_quality_ledger_status", cached_data_quality)

    report = await readiness._production_readiness()

    ledger = next(item for item in report["checks"] if item["id"] == "data_quality_ledger")
    assert report["status"] == "ok"
    assert ledger["ok"] is True
    assert ledger["evidence"]["cache"]["used"] is True
    assert ledger["evidence"]["live_probe_error"].startswith("TimeoutError")


@pytest.mark.asyncio
async def test_production_readiness_bounds_slow_action_queue_as_warning(monkeypatch):
    async def fake_health():
        return _healthy_health()

    async def fake_collector():
        return _healthy_collector()

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def slow_action_queue():
        await asyncio.sleep(999)

    monkeypatch.setenv("ANALYZER_READINESS_ACTION_QUEUE_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", fake_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)
    monkeypatch.setattr(readiness, "_collector_action_queue_status", slow_action_queue)

    report = await readiness._production_readiness()

    collector = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")
    queue = next(item for item in report["checks"] if item["id"] == "collector_action_queue_visible")
    assert report["status"] == "ok"
    assert collector["ok"] is True
    assert queue["ok"] is False
    assert queue["evidence"]["timeout_seconds"] == 0.01


@pytest.mark.asyncio
async def test_production_readiness_bounds_collector_fallback_total_timeout(monkeypatch):
    seen: list[float] = []

    async def fake_health():
        return _healthy_health()

    async def slow_collector():
        raise asyncio.TimeoutError()

    async def slow_fallback():
        await asyncio.sleep(999)

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    real_wait_for = asyncio.wait_for

    async def fake_wait_for(coro, timeout):
        seen.append(timeout)
        if timeout == 12.0:
            try:
                coro.close()
            except AttributeError:
                pass
            raise asyncio.TimeoutError()
        return await real_wait_for(coro, timeout=timeout)

    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", slow_collector)
    monkeypatch.setattr(readiness, "_collector_status_fallback", slow_fallback)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)
    monkeypatch.setattr(readiness.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setenv("ANALYZER_READINESS_COLLECTOR_RETRY_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("ANALYZER_READINESS_COLLECTOR_FALLBACK_TOTAL_TIMEOUT_SECONDS", "12")

    report = await readiness._production_readiness()

    collector = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")
    assert 12.0 in seen
    assert collector["ok"] is False
    assert "fallback_error" in collector["evidence"]["summary"]


def test_build_readiness_report_degrades_on_supabase_backlog_and_raw_mirror():
    health = _healthy_health()
    health["supabase_export"] = {
        "ok": False,
        "state": "backlog",
        "ready_to_export": 4,
        "exported_count": 2368,
        "raw_mirror": True,
    }

    report = readiness.build_readiness_report(health, _healthy_collector())
    supabase = next(item for item in report["checks"] if item["id"] == "supabase_populated")

    assert report["ok"] is False
    assert report["status"] == "degraded"
    assert supabase["ok"] is False
    assert supabase["evidence"]["ready_to_export"] == 4


def test_build_readiness_report_degrades_when_supabase_remote_readback_missing_or_empty():
    health = _healthy_health()
    health["supabase_export"]["remote_readback"] = {
        "configured": True,
        "reachable": True,
        "table_exists": True,
        "row_count": 0,
    }

    report = readiness.build_readiness_report(health, _healthy_collector())
    supabase = next(item for item in report["checks"] if item["id"] == "supabase_populated")

    assert report["ok"] is False
    assert report["status"] == "degraded"
    assert supabase["ok"] is False
    assert supabase["evidence"]["remote_readback"]["row_count"] == 0


def test_build_readiness_report_degrades_when_supabase_remote_count_lags_local_export():
    health = _healthy_health()
    health["supabase_export"]["exported_count"] = 2368
    health["supabase_export"]["remote_readback"]["row_count"] = 1200

    report = readiness.build_readiness_report(health, _healthy_collector())
    supabase = next(item for item in report["checks"] if item["id"] == "supabase_populated")

    assert report["ok"] is False
    assert report["status"] == "degraded"
    assert supabase["ok"] is False
    assert supabase["evidence"]["exported_count"] == 2368
    assert supabase["evidence"]["remote_readback"]["row_count"] == 1200


def test_build_readiness_report_degrades_on_hard_realtime_failure():
    collector = _healthy_collector()
    collector["summary"]["realtime_failed_sources"] = [
        {"source": "instagram", "failed": 1, "too_large": 0, "local_fallback": 0}
    ]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "degraded"
    assert collector_check["ok"] is False


def test_build_readiness_report_degrades_on_browser_source_issues():
    collector = _healthy_collector()
    collector["summary"]["source_issues"] = 1
    collector["summary"]["hard_source_issues"] = 1
    collector["summary"]["source_issue_samples"] = [
        {"source": "x", "status": "degraded", "detail": "browser content progress is stale"}
    ]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "degraded"
    assert collector_check["ok"] is False
    assert collector_check["evidence"]["summary"]["source_issues"] == 1


def test_build_readiness_report_accepts_diagnostic_only_browser_timeout_with_maintenance_ok():
    collector = _healthy_collector()
    collector["summary"].update({
        "dashboard_health_status": "degraded",
        "dashboard_health_effective_status": "ok",
        "source_issues": 1,
        "hard_source_issues": 0,
        "source_issue_samples": [
            {"source": "browser_extension", "status": "unknown", "message": "TimeoutError"}
        ],
        "browser_ingest_state": "unknown",
        "browser_ingest_effective_state": "active_via_maintenance",
        "browser_maintenance_state": "ok",
    })

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector_check["ok"] is True


def test_build_readiness_report_accepts_browser_diagnostic_timeout_when_yield_is_live():
    collector = _healthy_collector()
    collector["summary"].update({
        "dashboard_health_status": "degraded",
        "dashboard_health_effective_status": "degraded",
        "source_issues": 1,
        "hard_source_issues": 0,
        "source_issue_samples": [
            {
                "source": "browser_extension",
                "status": "unknown",
                "message": "browser extension diagnostics unavailable: TimeoutError",
            }
        ],
        "browser_ingest_state": "unknown",
        "browser_ingest_effective_state": "unknown",
        "browser_maintenance_state": "running",
        "browser_maintenance_last_terminal_state": "degraded",
        "browser_maintenance_detail": "maintenance pass started",
    })

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector_check["ok"] is True
    assert collector_check["evidence"]["summary"]["source_issues"] == 1


def test_build_readiness_report_accepts_collector_fallback_with_rolling_yield():
    collector = _healthy_collector()
    collector["summary"].update({
        "primary_error": "TimeoutError: ",
        "dashboard_health_status": None,
        "dashboard_health_effective_status": None,
        "browser_ingest_state": None,
        "browser_ingest_effective_state": None,
        "browser_maintenance_state": None,
        "browser_maintenance_last_terminal_state": None,
    })

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector_check["ok"] is True


def test_build_readiness_report_accepts_warning_only_browser_page_error():
    collector = _healthy_collector()
    collector["summary"].update({
        "browser_extension_issues": 1,
        "browser_extension_issue_samples": [
            {
                "platform": "x",
                "kind": "browser_page_error",
                "severity": "warning",
                "health_reason": "try_again_empty_state",
            }
        ],
    })
    collector["summary"]["media_yield_current_hour"][3]["stored_rolling_60m"] = 17

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector_check["ok"] is True


def test_build_readiness_report_accepts_warning_only_source_and_browser_rows():
    collector = _healthy_collector()
    collector["summary"].update({
        "dashboard_health_status": "degraded",
        "dashboard_health_effective_status": "degraded",
        "source_issues": 2,
        "hard_source_issues": 2,
        "source_issue_samples": [
            {
                "source": "facebook",
                "status": "degraded",
                "status_severity": "warning",
                "detail": "browser content progress is stale",
                "blocker": {"kind": "browser_capture_stalled", "severity": "warning"},
            },
            {
                "source": "x",
                "status": "degraded",
                "status_severity": "warning",
                "detail": "browser content progress is stale",
                "blocker": {"kind": "browser_page_error", "severity": "warning"},
            },
        ],
        "browser_extension_issues": 1,
        "browser_extension_issue_samples": [
            {
                "platform": "x",
                "kind": "browser_page_error",
                "severity": "warning",
                "health_reason": "try_again_empty_state",
            }
        ],
        "browser_ingest_state": "active_via_maintenance",
        "browser_ingest_effective_state": "active_via_maintenance",
        "browser_maintenance_state": "degraded",
        "browser_maintenance_last_terminal_state": "degraded",
        "browser_maintenance_detail": "maintenance loop sleeping after nonzero pass",
    })

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector_check["ok"] is True
    assert collector_check["evidence"]["summary"]["source_issues"] == 2
    assert collector_check["evidence"]["summary"]["browser_extension_issues"] == 1


def test_build_readiness_report_accepts_stale_maintenance_row_when_current_browser_evidence_is_clean():
    collector = _healthy_collector()
    collector["summary"].update({
        "dashboard_health_status": "ok",
        "dashboard_health_effective_status": "ok",
        "source_issues": 0,
        "hard_source_issues": 0,
        "browser_extension_issues": 0,
        "browser_ingest_state": "active_via_maintenance",
        "browser_ingest_effective_state": "active_via_maintenance",
        "browser_maintenance_state": "degraded",
        "browser_maintenance_last_terminal_state": "degraded",
        "browser_maintenance_detail": "browser extension tabs unhealthy after reload/profile restart",
    })

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector_check["ok"] is True
    assert collector_check["evidence"]["summary"]["browser_maintenance_state"] == "degraded"


def test_build_readiness_report_accepts_quota_budget_pause_as_visible_nonfailure():
    collector = _healthy_collector()
    collector["summary"]["quota_paused"] = 1
    collector["summary"]["quota_paused_samples"] = [
        {
            "service": "youtube",
            "reason": "target_budget_reached",
            "reset_at": "2026-08-21T15:00:00+08:00",
        }
    ]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector_check["ok"] is True
    assert collector_check["evidence"]["summary"]["quota_paused"] == 1


def test_build_readiness_report_surfaces_hourly_yield_warning_without_failing_critical_status():
    collector = _healthy_collector()
    collector["summary"]["media_yield_current_hour"] = [
        {"source": "facebook", "media_current_hour": 8, "records_current_hour": 8, "stored_rolling_60m": 2},
        {"source": "instagram", "media_current_hour": 0, "records_current_hour": 0, "stored_rolling_60m": 8},
        {"source": "threads", "media_current_hour": 0, "records_current_hour": 0, "stored_rolling_60m": 7},
        {"source": "x", "media_current_hour": 9, "records_current_hour": 9, "stored_rolling_60m": 3},
    ]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    yield_check = next(item for item in report["checks"] if item["id"] == "collector_hourly_yield_floor")

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert yield_check["ok"] is False
    assert yield_check["severity"] == "warning"
    assert [item["source"] for item in yield_check["evidence"]["failing"]] == ["facebook", "x"]
    assert yield_check["evidence"]["failing"][0]["useful_basis"] == "stored_rolling_60m"


def test_build_readiness_report_exempts_rate_limited_hourly_yield_source():
    collector = _healthy_collector()
    collector["summary"]["browser_ingest_active_platforms"] = ["tiktok"]
    collector["summary"]["media_yield_current_hour"] = [
        {
            "source": "tiktok",
            "media_current_hour": 0,
            "records_current_hour": 0,
            "stored_rolling_60m": 0,
            "rate_limits_current_hour": 2,
            "exempt": True,
        },
    ]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    yield_check = next(item for item in report["checks"] if item["id"] == "collector_hourly_yield_floor")

    assert yield_check["ok"] is True
    assert yield_check["evidence"]["exempt"][0]["source"] == "tiktok"


def test_build_readiness_report_exempts_active_rate_limit_object_without_blocker():
    collector = _healthy_collector()
    collector["summary"]["browser_ingest_active_platforms"] = ["tiktok"]
    collector["summary"]["media_yield_current_hour"] = [
        {
            "source": "tiktok",
            "media_current_hour": 0,
            "records_current_hour": 0,
            "stored_rolling_60m": 2,
            "rate_limits_current_hour": 0,
            "access_errors_current_hour": 0,
            "exempt": False,
            "blocker": {"kind": "none", "severity": "ok"},
            "rate_limit": {
                "active_now": True,
                "active_until": "2026-08-21T21:14:28.931926+00:00",
                "latest_reason": "local tool output matched challenge/block signature",
            },
        },
    ]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    yield_check = next(item for item in report["checks"] if item["id"] == "collector_hourly_yield_floor")

    assert yield_check["ok"] is True
    assert yield_check["evidence"]["exempt"][0]["source"] == "tiktok"
    assert yield_check["evidence"]["exempt"][0]["rate_limit"]["active_now"] is True


def test_build_readiness_report_counts_backend_records_when_browser_stored_rolling_is_zero():
    collector = _healthy_collector()
    collector["summary"]["browser_ingest_active_platforms"] = ["lemon8"]
    collector["summary"]["media_yield_current_hour"] = [
        {
            "source": "lemon8",
            "status": "live",
            "collection_mode": "headless/backend + optional extension probes",
            "media_current_hour": 0,
            "records_current_hour": 6,
            "stored_rolling_60m": 0,
            "observed_rolling_60m": 11,
            "requests_rolling_60m": 37,
            "blocker": {"kind": "none", "severity": "ok"},
            "exempt": False,
        },
    ]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    yield_check = next(item for item in report["checks"] if item["id"] == "collector_hourly_yield_floor")

    assert yield_check["ok"] is True
    assert yield_check["evidence"]["passing"][0]["source"] == "lemon8"
    assert yield_check["evidence"]["passing"][0]["useful_basis"] == "current_hour_backend_fallback"


def test_build_readiness_report_does_not_exempt_stats_unavailable_without_useful_output():
    collector = _healthy_collector()
    collector["summary"]["browser_ingest_active_platforms"] = ["facebook", "tiktok"]
    collector["summary"]["media_yield_current_hour"] = [
        {
            "source": "facebook",
            "stored_rolling_60m": 20,
            "exempt": True,
            "blocker": {
                "kind": "stats_unavailable",
                "severity": "ok",
                "summary": "source matrix timed out",
            },
        },
        {
            "source": "tiktok",
            "stored_rolling_60m": 0,
            "exempt": True,
            "blocker": {
                "kind": "stats_unavailable",
                "severity": "ok",
                "summary": "source matrix timed out",
            },
        },
    ]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    yield_check = next(item for item in report["checks"] if item["id"] == "collector_hourly_yield_floor")

    assert report["status"] == "ok"
    assert yield_check["ok"] is False
    assert [item["source"] for item in yield_check["evidence"]["passing"]] == ["facebook"]
    assert yield_check["evidence"]["failing"][0]["source"] == "tiktok"
    assert "stats unavailable" in yield_check["evidence"]["failing"][0]["reason"]
    assert yield_check["evidence"]["exempt"] == []


def test_build_readiness_report_does_not_exempt_old_24h_rate_limit_without_current_blocker():
    collector = _healthy_collector()
    collector["summary"]["browser_ingest_active_platforms"] = ["x"]
    collector["summary"]["media_yield_current_hour"] = [
        {
            "source": "x",
            "media_current_hour": 0,
            "records_current_hour": 3,
            "stored_rolling_60m": 3,
            "rate_limits_current_hour": 0,
            "access_errors_current_hour": 0,
            "rate_limits_24h": 2,
            "access_errors_24h": 0,
            "exempt": False,
        },
    ]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    yield_check = next(item for item in report["checks"] if item["id"] == "collector_hourly_yield_floor")

    assert yield_check["ok"] is False
    assert yield_check["evidence"]["failing"][0]["source"] == "x"
    assert yield_check["evidence"]["exempt"] == []


def test_build_readiness_report_excludes_strava_from_default_social_media_yield_floor():
    collector = _healthy_collector()

    report = readiness.build_readiness_report(_healthy_health(), collector)
    yield_check = next(item for item in report["checks"] if item["id"] == "collector_hourly_yield_floor")

    assert "strava" not in yield_check["evidence"]["monitored_sources"]
    assert all(item["source"] != "strava" for item in yield_check["evidence"]["failing"])


def test_build_readiness_report_can_monitor_strava_when_explicitly_configured(monkeypatch):
    monkeypatch.setenv("COLLECTOR_READINESS_HOURLY_YIELD_SOURCES", "strava")
    collector = _healthy_collector()

    report = readiness.build_readiness_report(_healthy_health(), collector)
    yield_check = next(item for item in report["checks"] if item["id"] == "collector_hourly_yield_floor")

    assert yield_check["ok"] is False
    assert yield_check["evidence"]["monitored_sources"] == ["strava"]
    assert yield_check["evidence"]["failing"][0]["source"] == "strava"


def test_build_readiness_report_degrades_when_browser_diagnostics_missing():
    collector = _healthy_collector()
    collector["summary"]["dashboard_health_status"] = None
    collector["summary"]["dashboard_health_effective_status"] = None
    collector["summary"]["browser_ingest_state"] = None
    collector["summary"]["browser_ingest_effective_state"] = None

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "degraded"
    assert collector_check["ok"] is False


def test_build_readiness_report_degrades_on_browser_maintenance_failure():
    collector = _healthy_collector()
    collector["summary"]["browser_maintenance_state"] = "degraded"
    collector["summary"]["browser_maintenance_detail"] = "browser extension tabs unhealthy after targeted reload"

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "degraded"
    assert collector_check["ok"] is False
    assert "unhealthy" in collector_check["evidence"]["summary"]["browser_maintenance_detail"]


def test_build_readiness_report_accepts_running_browser_maintenance_after_ok_pass():
    collector = _healthy_collector()
    collector["summary"]["browser_maintenance_state"] = "running"
    collector["summary"]["browser_maintenance_last_terminal_state"] = "ok"

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector_check["ok"] is True


def test_build_readiness_report_accepts_running_nonstalled_maintenance_with_clean_current_browser_evidence():
    collector = _healthy_collector()
    collector["summary"].update({
        "dashboard_health_status": "ok",
        "dashboard_health_effective_status": "ok",
        "source_issues": 0,
        "hard_source_issues": 0,
        "browser_extension_issues": 0,
        "browser_ingest_state": "active",
        "browser_ingest_effective_state": "active",
        "browser_maintenance_state": "running",
        "browser_maintenance_last_terminal_state": "degraded",
        "browser_maintenance_detail": "maintenance pass started",
        "browser_maintenance_stale": False,
        "browser_maintenance_running_stalled": False,
        "browser_maintenance_running_without_active_pass": False,
    })

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector_check["ok"] is True


def test_build_readiness_report_degrades_on_missing_cookie_vault_auth():
    collector = _healthy_collector()
    collector["summary"]["cookie_vault_missing_auth_platforms"] = ["facebook", "instagram"]

    report = readiness.build_readiness_report(_healthy_health(), collector)
    collector_check = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "degraded"
    assert collector_check["ok"] is False
    assert collector_check["evidence"]["summary"]["cookie_vault_missing_auth_platforms"] == [
        "facebook",
        "instagram",
    ]


def test_build_readiness_report_degrades_on_stale_face_processing():
    health = _healthy_health()
    health["face_processing"] = {
        "available": True,
        "ok": False,
        "state": "stale",
        "latest_image_age_seconds": 999999,
    }

    report = readiness.build_readiness_report(health, _healthy_collector())
    face_processing = next(item for item in report["checks"] if item["id"] == "face_processing_fresh")

    assert report["status"] == "degraded"
    assert face_processing["ok"] is False
    assert face_processing["evidence"]["state"] == "stale"


@pytest.mark.asyncio
async def test_production_readiness_returns_collector_failure_surface(monkeypatch):
    async def fake_health():
        return _healthy_health()

    async def fail_collector():
        raise TimeoutError("collector slow")

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def fail_fallback():
        raise RuntimeError("fallback slow")

    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", fail_collector)
    monkeypatch.setattr(readiness, "_collector_status_fallback", fail_fallback)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)

    report = await readiness._production_readiness()
    collector = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "degraded"
    assert collector["ok"] is False
    assert collector["evidence"]["collector_dashboard"] == "unreachable"
    assert "TimeoutError" in collector["evidence"]["summary"]["error"]
    assert "RuntimeError" in collector["evidence"]["summary"]["fallback_error"]


@pytest.mark.asyncio
async def test_production_readiness_fetches_health_and_collector_in_parallel(monkeypatch):
    async def slow_health():
        await asyncio.sleep(0.02)
        return _healthy_health()

    async def slow_collector():
        await asyncio.sleep(0.02)
        return _healthy_collector()

    async def slow_supabase():
        await asyncio.sleep(0.02)
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    monkeypatch.setattr(readiness, "_health_status", slow_health)
    monkeypatch.setattr(readiness, "_collector_status", slow_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", slow_supabase)

    start = asyncio.get_running_loop().time()
    report = await readiness._production_readiness()
    elapsed = asyncio.get_running_loop().time() - start

    assert report["ok"] is True
    assert elapsed < 0.15


@pytest.mark.asyncio
async def test_production_readiness_bounds_slow_collector_status(monkeypatch):
    async def fake_health():
        return _healthy_health()

    async def slow_collector():
        await asyncio.sleep(0.05)
        return _healthy_collector()

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    async def fail_fallback():
        raise RuntimeError("fallback slow")

    monkeypatch.setenv("ANALYZER_READINESS_COLLECTOR_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("ANALYZER_READINESS_COLLECTOR_RETRY_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", slow_collector)
    monkeypatch.setattr(readiness, "_collector_status_fallback", fail_fallback)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)

    report = await readiness._production_readiness()
    collector = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "degraded"
    assert collector["ok"] is False
    assert collector["evidence"]["collector_dashboard"] == "unreachable"
    assert "TimeoutError" in collector["evidence"]["summary"]["error"]


@pytest.mark.asyncio
async def test_production_readiness_uses_isolated_collector_retry_after_primary_timeout(monkeypatch):
    async def fake_health():
        return _healthy_health()

    calls = 0

    async def flaky_collector():
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(999)
        result = _healthy_collector()
        result["collector_dashboard"] = "ok"
        return result

    async def fail_fallback():
        raise AssertionError("fallback should not run when isolated collector retry succeeds")

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    monkeypatch.setenv("ANALYZER_READINESS_COLLECTOR_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("ANALYZER_READINESS_COLLECTOR_RETRY_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", flaky_collector)
    monkeypatch.setattr(readiness, "_collector_status_fallback", fail_fallback)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)

    report = await readiness._production_readiness()
    collector = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector["ok"] is True
    assert collector["evidence"]["summary"]["primary_error"].startswith("TimeoutError")
    assert collector["evidence"]["summary"]["retry_timeout_seconds"] == 1.0


@pytest.mark.asyncio
async def test_production_readiness_uses_collector_fallback_after_primary_timeout(monkeypatch):
    async def fake_health():
        return _healthy_health()

    async def slow_collector():
        await asyncio.sleep(0.05)
        return _healthy_collector()

    async def fallback_collector():
        result = _healthy_collector()
        result["proof_path"] = "fallback_dashboard_health"
        result["summary"]["primary_error"] = "TimeoutError: "
        return result

    async def fake_supabase():
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    monkeypatch.setenv("ANALYZER_READINESS_COLLECTOR_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("ANALYZER_READINESS_COLLECTOR_RETRY_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", slow_collector)
    monkeypatch.setattr(readiness, "_collector_status_fallback", fallback_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", fake_supabase)

    report = await readiness._production_readiness()
    collector = next(item for item in report["checks"] if item["id"] == "collector_production_surfaces")

    assert report["status"] == "ok"
    assert collector["ok"] is True
    assert collector["evidence"]["summary"]["primary_error"].startswith("TimeoutError")


@pytest.mark.asyncio
async def test_production_readiness_bounds_slow_supabase_readback(monkeypatch):
    async def fake_health():
        return _healthy_health()

    async def fake_collector():
        return _healthy_collector()

    async def slow_supabase():
        await asyncio.sleep(0.05)
        return {
            "configured": True,
            "reachable": True,
            "table_exists": True,
            "row_count": 2368,
        }

    monkeypatch.setenv("ANALYZER_READINESS_SUPABASE_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(readiness, "_health_status", fake_health)
    monkeypatch.setattr(readiness, "_collector_status", fake_collector)
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", slow_supabase)

    report = await readiness._production_readiness()
    supabase = next(item for item in report["checks"] if item["id"] == "supabase_populated")

    assert report["status"] == "degraded"
    assert supabase["ok"] is False
    assert supabase["evidence"]["remote_readback"]["reachable"] is False
    assert "TimeoutError" in supabase["evidence"]["remote_readback"]["error"]
