import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from contextvars import ContextVar
from unittest.mock import AsyncMock

import asyncpg
import pytest

from src.api.routes import health, readiness
from src.db import connection


class _Connection:
    def __init__(self) -> None:
        self.busy = False

    async def fetchval(self, sql: str) -> int:
        return 1

    async def fetchrow(self, sql: str, *args: str) -> None:
        if self.busy:
            raise asyncpg.InterfaceError("another operation is in progress")
        self.busy = True
        try:
            await asyncio.sleep(0)
        finally:
            self.busy = False


class _Pool:
    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_Connection]:
        yield _Connection()


@pytest.mark.asyncio
async def test_readiness_fallback_serializes_queries_on_each_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty_probe(conn: _Connection) -> dict[str, bool]:
        return {"ok": True}

    async def empty_audit(conn: _Connection, *, sample_limit: int) -> dict[str, bool]:
        return {"ok": True}

    monkeypatch.setattr(connection, "get_analyzer_pool", _Pool)
    monkeypatch.setattr(connection, "get_collector_pool", _Pool)
    monkeypatch.setattr(health, "_supabase_export_health", empty_probe)
    monkeypatch.setattr(health, "_face_processing_health", empty_probe)
    monkeypatch.setattr(health, "audit_face_bridge_collisions", empty_audit)

    result = await readiness._health_status_fast_fallback(TimeoutError(), 20.0)

    assert "fallback_errors" not in result, result.get("fallback_errors")
    assert result["scheduler_freshness"]["incremental"]["ok"] is False
    assert result["scheduler_freshness"]["full_resolution"]["ok"] is False


@pytest.mark.asyncio
async def test_primary_readiness_does_not_wait_for_dashboard_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stalled_dashboard_health() -> None:
        await asyncio.Event().wait()

    async def critical_health(original_error: Exception | None, timeout_seconds: float) -> dict[str, str]:
        return {"analyzer_db": "connected", "collector_db": "connected"}

    monkeypatch.setattr(health, "health_check", stalled_dashboard_health)
    monkeypatch.setattr(readiness, "_health_status_fast_fallback", critical_health)

    try:
        result = await asyncio.wait_for(readiness._health_status(), timeout=1.0)
    except TimeoutError:
        pytest.fail("readiness waited on dashboard aggregates despite available critical health proof")

    assert result == {"analyzer_db": "connected", "collector_db": "connected"}


@pytest.mark.asyncio
async def test_slow_face_audit_preserves_other_primary_health_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    async def healthy_probe(conn: _Connection) -> dict[str, bool]:
        return {"ok": True}

    async def stalled_audit(conn: _Connection, *, sample_limit: int) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(connection, "get_analyzer_pool", _Pool)
    monkeypatch.setattr(connection, "get_collector_pool", _Pool)
    monkeypatch.setattr(health, "_supabase_export_health", healthy_probe)
    monkeypatch.setattr(health, "_face_processing_health", healthy_probe)
    monkeypatch.setattr(health, "audit_face_bridge_collisions", stalled_audit)
    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_FALLBACK_TIMEOUT_SECONDS", "2")

    try:
        result = await asyncio.wait_for(readiness._health_status(), timeout=0.4)
    except TimeoutError:
        pytest.fail("A slow face audit discarded completed primary readiness evidence")

    assert result["analyzer_db"] == "connected"
    assert result["collector_db"] == "connected"
    assert result["supabase_export"]["ok"] is True
    assert result["face_processing"]["ok"] is True
    assert result["status"] == "degraded"
    assert result["fallback_errors"]


@pytest.mark.parametrize("evidence", [{}, {"pending_jsonl": 0}, {"jsonl_errors": 0}])
def test_missing_decision_log_evidence_cannot_pass(evidence: dict[str, int]) -> None:
    result = readiness.build_readiness_report({"decision_log": evidence}, {})
    check = next(item for item in result["checks"] if item["id"] == "decision_log_durable")
    assert check["ok"] is False, "Missing decision durability counters must not be treated as zero"


@pytest.mark.asyncio
async def test_decision_log_query_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    class _FailingDecisionLogConnection:
        async def fetchval(self, sql: str) -> int:
            return 1

        async def fetchrow(self, sql: str, *args: str) -> None:
            if "audit_log" in sql:
                raise asyncpg.PostgresError('relation "audit_log" does not exist')
            return None

    class _FailingDecisionLogPool:
        @asynccontextmanager
        async def acquire(self) -> AsyncIterator[_FailingDecisionLogConnection]:
            yield _FailingDecisionLogConnection()

    async def empty_probe(conn: object) -> dict[str, bool]:
        return {"ok": True}

    async def empty_audit(conn: object, *, sample_limit: int) -> dict[str, bool]:
        return {"ok": True}

    monkeypatch.setattr(connection, "get_analyzer_pool", _FailingDecisionLogPool)
    monkeypatch.setattr(connection, "get_collector_pool", _Pool)
    monkeypatch.setattr(health, "_supabase_export_health", empty_probe)
    monkeypatch.setattr(health, "_face_processing_health", empty_probe)
    monkeypatch.setattr(health, "audit_face_bridge_collisions", empty_audit)

    with caplog.at_level(logging.WARNING, logger="src.api.routes.readiness"):
        result = await readiness._health_status_fast_fallback(TimeoutError(), 20.0)

    assert result.get("decision_log", {}) == {}, "a failed query must not fabricate zero counters"
    assert any(
        "decision_log" in record.message.lower() or "audit_log" in record.message.lower()
        for record in caplog.records
    ), "a failed decision-log query must be logged, not silently swallowed"

def test_missing_export_backlog_evidence_cannot_pass() -> None:
    export = {
        "ok": True, "exported_count": 10, "raw_mirror": False,
        "remote_readback": {"reachable": True, "table_exists": True, "row_count": 10},
    }
    result = readiness.build_readiness_report({"supabase_export": export}, {})
    check = next(item for item in result["checks"] if item["id"] == "supabase_populated")
    assert check["ok"] is False, "Missing export backlog evidence must not be treated as zero"


@pytest.mark.asyncio
async def test_completed_incremental_evidence_survives_stalled_full_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def freshness(conn: _Connection, run_type: str, **kwargs: int) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"ok": True}
        await asyncio.Event().wait()
        return {"ok": False}

    monkeypatch.setattr(connection, "get_analyzer_pool", _Pool)
    monkeypatch.setattr(connection, "get_collector_pool", _Pool)
    monkeypatch.setattr(health, "_run_freshness", freshness)
    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS", "0.2")
    result = await readiness._health_status()
    assert result["scheduler_freshness"]["incremental"].get("ok") is True, "Completed incremental proof was discarded"


@pytest.mark.asyncio
@pytest.mark.parametrize("markers", [(11,), (11, 29)], ids=["outer-timeout", "concurrent-requests"])
async def test_outer_timeout_retains_request_local_health_progress(
    monkeypatch: pytest.MonkeyPatch, markers: tuple[int, ...],
) -> None:
    marker = ContextVar("test_readiness_request", default=0)
    release_gate = asyncio.Event()

    class SlowReleasePool:
        @asynccontextmanager
        async def acquire(self) -> AsyncIterator[_Connection]:
            try:
                yield _Connection()
            finally:
                try:
                    await release_gate.wait()
                except asyncio.CancelledError:
                    await asyncio.sleep(1)

    async def export(conn: _Connection) -> dict[str, int | bool]:
        return {"ok": True, "ready_to_export": 0, "exported_count": marker.get(), "raw_mirror": False}

    async def stalled_audit(conn: _Connection, *, sample_limit: int) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(connection, "get_analyzer_pool", SlowReleasePool)
    monkeypatch.setattr(connection, "get_collector_pool", SlowReleasePool)
    monkeypatch.setattr(health, "_supabase_export_health", export)
    monkeypatch.setattr(health, "_face_processing_health", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(health, "audit_face_bridge_collisions", stalled_audit)
    for name in (
        "_collector_status", "_data_quality_ledger_status", "_collector_action_queue_status",
        "_analyst_workflow_status", "_analyst_value_path_status",
    ):
        monkeypatch.setattr(readiness, name, AsyncMock(return_value={}))
    monkeypatch.setattr(readiness, "_supabase_remote_readback_status", AsyncMock(return_value={
        "configured": True, "reachable": True, "table_exists": True, "row_count": 100,
    }))
    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("ANALYZER_READINESS_TOTAL_BUDGET_SECONDS", "0.5")

    async def run_probe(value: int):
        token = marker.set(value)
        try:
            return await readiness._production_readiness()
        finally:
            marker.reset(token)

    probes = [asyncio.create_task(run_probe(value)) for value in markers]
    try:
        _, pending = await asyncio.wait(probes, timeout=3)
    finally:
        release_gate.set()
        for probe in probes:
            if not probe.done():
                probe.cancel()
        reports = await asyncio.gather(*probes, return_exceptions=True)
    assert not pending, "Readiness waited indefinitely for cancellation cleanup"

    for report, expected in zip(reports, markers, strict=True):
        assert isinstance(report, dict)
        checks = {check["id"]: check for check in report["checks"]}
        assert checks["databases_connected"]["ok"] is True, "Outer timeout discarded completed connection evidence"
        assert checks["supabase_populated"]["evidence"].get("exported_count") == expected, "Request-local export evidence was lost or mixed"
        assert checks["face_identity_safety"]["ok"] is False
        assert report["status"] == "degraded"


@pytest.mark.asyncio
async def test_readiness_deadline_includes_cancelled_probe_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_started, release = asyncio.Event(), asyncio.Event()

    async def slow_health() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release.wait()

    monkeypatch.setattr(readiness, "_health_status", slow_health)
    for name in (
        "_collector_status", "_supabase_remote_readback_status", "_data_quality_ledger_status",
        "_collector_action_queue_status", "_analyst_workflow_status", "_analyst_value_path_status",
    ):
        monkeypatch.setattr(readiness, name, AsyncMock(return_value={}))
    monkeypatch.setenv("ANALYZER_READINESS_HEALTH_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("ANALYZER_READINESS_TOTAL_BUDGET_SECONDS", "0.2")
    probe = asyncio.create_task(readiness._production_readiness())
    try:
        done, _ = await asyncio.wait({probe}, timeout=1)
    finally:
        release.set()
        if not probe.done():
            probe.cancel()
        await asyncio.gather(probe, return_exceptions=True)
    assert cleanup_started.is_set()
    assert probe in done, "Readiness deadline waited for stalled cancellation cleanup"
    report = probe.result()
    assert report["status"] == "degraded"
    assert report["summary"]["critical_failed"] > 0
