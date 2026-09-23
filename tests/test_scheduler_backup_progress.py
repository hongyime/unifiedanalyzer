"""A slow database backup must not serialize scheduled analysis behind it."""

from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from src.pipeline import recon_bridge
from src.db.backup import BackupConfig, BackupRunResult
from src.scheduler import scheduler


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline_ticks", [1, 3])
async def test_pipeline_advances_while_backup_is_pending(
    monkeypatch: pytest.MonkeyPatch, pipeline_ticks: int,
) -> None:
    # Given a backup that cannot finish until the pipeline has made progress.
    started = anyio.Event()
    release = anyio.Event()
    backup_calls = 0
    analysis_calls = 0
    now = datetime.now(timezone.utc)

    class Clock:
        calls = 0

        @classmethod
        def now(cls, tz: timezone) -> datetime:
            cls.calls += 1
            return now + timedelta(minutes=10 * cls.calls)

    async def pending_backup(_now: datetime) -> None:
        nonlocal backup_calls
        backup_calls += 1
        started.set()
        await release.wait()

    async def pipeline() -> dict[str, bool]:
        nonlocal analysis_calls
        await started.wait()
        analysis_calls += 1
        if analysis_calls == pipeline_ticks:
            release.set()
            scheduler.stop_scheduler()
        return {"completed": True}

    for name in (
        "clear_orphaned_run_locks", "_build_daily_digest", "notify_daily_digest",
        "build_identity_digest", "notify_identity_digest", "_push_new_alerts",
        "_check_collector_health", "notify_collector_health", "_check_merge_candidates",
        "_run_decision_outbox_check", "_stage_collector_priority_hints",
        "_stage_identity_truth_and_indicators",
        "run_scheduler_heartbeat",
    ):
        monkeypatch.setattr(scheduler, name, AsyncMock())
    monkeypatch.setattr(scheduler, "check_db_connectivity", AsyncMock(return_value=True))
    monkeypatch.setattr(scheduler, "get_last_run_time", AsyncMock(return_value=now))
    monkeypatch.setattr(scheduler, "datetime", Clock)
    monkeypatch.setattr(scheduler, "_run_db_backup_check", pending_backup)
    monkeypatch.setattr(scheduler, "run_incremental", pipeline)
    monkeypatch.setattr(recon_bridge, "bridge_recon_observations", AsyncMock())
    monkeypatch.setenv("ANALYZER_DB_BACKUP_ENABLED", "1")
    monkeypatch.setenv("ANALYZER_DB_BACKUP_HOUR_UTC", "0")
    monkeypatch.setenv("ANALYZER_DB_BACKUP_CHECK_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("INCREMENTAL_RUN_INTERVAL_MINUTES", "0")
    monkeypatch.setenv("STATUS_HEARTBEAT_INTERVAL_HOURS", "0")
    monkeypatch.setenv("TELEGRAM_MERGE_BOT_ENABLED", "0")

    # When actual scheduler ticks run with a pending backup across due windows.
    try:
        with anyio.fail_after(3):
            await scheduler.start_scheduler()
    except TimeoutError:
        pytest.fail("Scheduled analysis was blocked behind the pending database backup")
    finally:
        release.set()
        scheduler.stop_scheduler()

    # Then analysis progresses and a second concurrent backup is never started.
    assert analysis_calls == pipeline_ticks
    assert backup_calls == 1, "Only one backup may be active across scheduler ticks"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_owner", [True, False], ids=["cancellation", "exception"])
async def test_scheduler_exit_joins_running_backup_thread(
    monkeypatch: pytest.MonkeyPatch, cancel_owner: bool,
) -> None:
    started = anyio.Event()
    fault = anyio.Event()
    release = Event()
    finished = Event()
    loop = scheduler.asyncio.get_running_loop()
    calls = 0
    now = datetime.now(timezone.utc)

    def blocking_dump(config: BackupConfig, *, now: datetime) -> BackupRunResult:
        loop.call_soon_threadsafe(started.set)
        try:
            if not release.wait(10):
                raise TimeoutError("Test backup was not released")
            return BackupRunResult((), (), (), ())
        finally:
            finished.set()

    async def connectivity() -> bool:
        nonlocal calls
        calls += 1
        if calls > 1:
            await fault.wait()
            raise RuntimeError("Controlled scheduler loop failure")
        return True

    async def pipeline() -> dict[str, bool]:
        await started.wait()
        if cancel_owner:
            await anyio.Event().wait()
        return {"completed": True}

    for name in (
        "clear_orphaned_run_locks", "_build_daily_digest", "notify_daily_digest",
        "build_identity_digest", "notify_identity_digest", "_push_new_alerts",
        "_check_collector_health", "notify_collector_health", "_check_merge_candidates",
        "_run_decision_outbox_check", "_stage_collector_priority_hints",
        "_stage_identity_truth_and_indicators",
        "run_scheduler_heartbeat",
    ):
        monkeypatch.setattr(scheduler, name, AsyncMock())
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(scheduler, "get_analyzer_pool", lambda: pool)
    monkeypatch.setattr(scheduler, "run_due_backups", blocking_dump)
    monkeypatch.setattr(scheduler, "check_db_connectivity", connectivity)
    monkeypatch.setattr(scheduler, "get_last_run_time", AsyncMock(return_value=now))
    monkeypatch.setattr(scheduler, "run_incremental", pipeline)
    monkeypatch.setattr(recon_bridge, "bridge_recon_observations", AsyncMock())
    monkeypatch.setenv("ANALYZER_DATABASE_URL", "postgres://unused@127.0.0.1/analyzer_ci")
    monkeypatch.setenv("ANALYZER_DB_BACKUP_ENABLED", "1")
    monkeypatch.setenv("ANALYZER_DB_BACKUP_HOUR_UTC", "0")
    monkeypatch.setenv("INCREMENTAL_RUN_INTERVAL_MINUTES", "0")
    monkeypatch.setenv("STATUS_HEARTBEAT_INTERVAL_HOURS", "0")
    monkeypatch.setenv("TELEGRAM_MERGE_BOT_ENABLED", "0")

    owner = scheduler.asyncio.create_task(scheduler.start_scheduler())
    exited_before_backup = False
    try:
        with anyio.fail_after(5):
            await started.wait()
            if cancel_owner:
                owner.cancel()
            else:
                fault.set()
            await anyio.wait_all_tasks_blocked()
            exited_before_backup = owner.done()
    finally:
        release.set()
        expected = scheduler.asyncio.CancelledError if cancel_owner else (RuntimeError, ExceptionGroup)
        with pytest.raises(expected):
            await owner
        assert await anyio.to_thread.run_sync(finished.wait, 5)
        scheduler.stop_scheduler()

    assert not exited_before_backup, "Scheduler exited while its backup thread was still running"
