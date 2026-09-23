from pathlib import Path

import anyio
import pytest
from anyio.abc import TaskGroup

from src.scheduler import scheduler


@pytest.mark.asyncio
async def test_scheduler_refreshes_heartbeat_during_wait_and_stops_writer_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes = 0
    refreshed = anyio.Event()

    def touch(path: Path, mode: int = 0o666, exist_ok: bool = True) -> None:
        nonlocal writes
        writes += 1
        if writes >= 3:
            refreshed.set()

    async def waiting_loop(background_tasks: TaskGroup) -> None:
        await refreshed.wait()

    monkeypatch.setattr(Path, "touch", touch)
    monkeypatch.setattr(scheduler, "_scheduler_loop", waiting_loop)
    monkeypatch.setattr(scheduler, "_HEARTBEAT_INTERVAL_SECONDS", 0.01, raising=False)

    try:
        with anyio.fail_after(2):
            await scheduler.start_scheduler()
    except TimeoutError:
        pytest.fail("Scheduler heartbeat did not refresh while its loop was waiting")

    assert writes >= 3
    assert not any(task.name == "scheduler_heartbeat" for task in anyio.get_running_tasks()), "Heartbeat writer outlived its scheduler"
