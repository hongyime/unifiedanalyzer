import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

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
