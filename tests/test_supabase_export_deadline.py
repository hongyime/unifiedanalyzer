"""Synthetic transport stalls must not leave export jobs waiting indefinitely."""
import asyncio
from datetime import datetime, timezone
import uuid

import pytest

from src.pipeline import indicator_export as export


class Local:
    def __init__(self, stall=None):
        self.stall = stall
        self.updates = []
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.rows = [dict(id=str(uuid.uuid4()), indicator_type="domain",
                         normalized_value="example.test", display_value="example.test",
                         source_families=["test"], evidence_count=1, confidence=1,
                         first_seen_at=now, last_seen_at=now, metadata={},
                         created_at=now, updated_at=now)]

    async def fetch(self, *args):
        return self.rows

    async def execute(self, sql, *args):
        state = "exported" if "export_status = 'exported'" in sql else "retry"
        if self.stall == state:
            await asyncio.Event().wait()
        self.updates.append(state)


class Remote:
    def __init__(self, stall=None):
        self.stall = stall
        self.closed = False
        self.terminated = False
        self.started = asyncio.Event()
        self.writes = 0

    async def step(self, name):
        self.started.set()
        if self.stall == name:
            await asyncio.Event().wait()

    async def execute(self, *args):
        await self.step("schema")

    async def executemany(self, *args):
        await self.step("upsert")
        self.writes += 1

    async def fetch(self, *args):
        await self.step("fetch")
        return []

    async def close(self):
        await self.step("close")
        self.closed = True

    def terminate(self):
        self.terminated = True


@pytest.fixture(autouse=True)
def short_deadlines(monkeypatch):
    monkeypatch.setenv("SUPABASE_OPERATION_TIMEOUT_SECONDS", "0.03")
    monkeypatch.setattr(export, "SUPABASE_CLEANUP_TIMEOUT_SECONDS", 0.03, raising=False)


async def run_export(local, remote, **kwargs):
    return await asyncio.wait_for(export.export_pending_supabase_indicators(
        local, mode="postgres_direct", remote_conn=remote, **kwargs), timeout=0.6)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["schema", "upsert"])
async def test_stalled_remote_returns_retry_without_acknowledging(stage):
    local, remote = Local(), Remote(stage)
    result = await run_export(local, remote)
    assert result["status"] == "error"
    assert "deadline" in result["error"].lower()
    assert result["exported"] == 0
    assert local.updates == ["retry"]
    assert not remote.closed and not remote.terminated  # borrowed connection


@pytest.mark.asyncio
async def test_stalled_local_acknowledgement_is_retryable():
    local, remote = Local("exported"), Remote()
    result = await run_export(local, remote)
    assert result["status"] == "error"
    assert result["exported"] == 0
    assert remote.writes == 1
    assert local.updates == ["retry"]


@pytest.mark.asyncio
async def test_stalled_retry_bookkeeping_also_returns():
    local = Local("retry")
    result = await run_export(local, Remote("upsert"))
    assert result["status"] == "error"
    assert result["retry_state_recorded"] is False
    assert local.updates == []


@pytest.mark.asyncio
async def test_owned_close_timeout_preserves_success(monkeypatch):
    local, remote = Local(), Remote("close")
    connect_options = {}

    async def connect(dsn, **kwargs):
        connect_options.update(kwargs)
        return remote

    monkeypatch.setattr(export.asyncpg, "connect", connect)
    result = await run_export(local, None, database_url="postgresql://unused")
    assert result["status"] == "ok" and result["exported"] == 1
    assert local.updates == ["exported"]
    assert remote.terminated
    assert connect_options["command_timeout"] == 0.03


@pytest.mark.asyncio
async def test_stalled_connect_is_inside_total_deadline(monkeypatch):
    async def connect(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(export.asyncpg, "connect", connect)
    local = Local()
    result = await run_export(local, None, database_url="postgresql://unused")
    assert result["status"] == "error"
    assert local.updates == ["retry"]


@pytest.mark.asyncio
async def test_reconciliation_read_stall_returns_without_cleanup_writes():
    remote = Remote("fetch")
    result = await asyncio.wait_for(export.reconcile_supabase_indicators(
        Local(), mode="postgres_direct", remote_conn=remote), timeout=0.6)
    assert result["status"] == "error" and result["deleted"] == 0
    assert "deadline" in result["error"].lower()
    assert remote.writes == 0 and not remote.closed


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_and_closes_owned_connection(monkeypatch):
    local, remote = Local(), Remote("schema")

    async def connect(*args, **kwargs):
        return remote

    monkeypatch.setattr(export.asyncpg, "connect", connect)
    task = asyncio.create_task(export.export_pending_supabase_indicators(
        local, mode="postgres_direct", database_url="postgresql://unused"))
    await remote.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert remote.closed and local.updates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,dry_run", [("disabled", False), ("write", True)])
async def test_disabled_and_preview_keep_remote_untouched(mode, dry_run):
    local, remote = Local(), Remote("schema")
    result = await export.export_pending_supabase_indicators(
        local, mode=mode, dry_run=dry_run, remote_conn=remote)
    assert result["status"] in {"skipped", "dry_run"}
    assert not remote.started.is_set() and local.updates == []


@pytest.mark.parametrize("raw,expected", [
    ("", 45), ("invalid", 45), ("0", 45), ("-1", 45),
    ("nan", 45), ("inf", 45), ("121", 120), ("12.5", 12.5),
])
def test_invalid_or_unlimited_settings_cannot_remove_deadline(monkeypatch, raw, expected):
    monkeypatch.setenv("SUPABASE_OPERATION_TIMEOUT_SECONDS", raw)
    assert export._supabase_operation_timeout() == expected


@pytest.mark.asyncio
async def test_schema_and_write_share_one_budget():
    class SlowRemote(Remote):
        async def step(self, name):
            await asyncio.sleep(0.02)

    local, remote = Local(), SlowRemote()
    result = await run_export(local, remote)
    assert result["status"] == "error" and result["exported"] == 0
    assert remote.writes == 0 and local.updates == ["retry"]
