"""
QA-lane tests: scheduler was_offline / reconnect path in start_scheduler.

Tests that:
1. When check_db_connectivity() returns False, the scheduler logs the warning
   and sets was_offline=True (only on the first offline tick).
2. When connectivity is restored, the scheduler logs the "restored" message
   and resets was_offline=False.
3. A second consecutive offline tick does NOT re-log the warning.

These cover lines 865-874 of src/scheduler/scheduler.py which had no test.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connectivity_sequence(*values: bool):
    """Return an AsyncMock that yields each bool in order, then stops _running."""
    call_count = 0

    async def _check():
        nonlocal call_count
        result = values[call_count] if call_count < len(values) else True
        call_count += 1
        return result

    return _check


def _base_scheduler_patches(connectivity_fn):
    """Minimum patches to let start_scheduler run a few ticks without DB."""
    return [
        # Connectivity is our primary control
        patch("src.scheduler.scheduler.check_db_connectivity", side_effect=connectivity_fn),
        # clear_orphaned_run_locks is called at startup
        patch("src.scheduler.scheduler.clear_orphaned_run_locks", new_callable=AsyncMock),
        # Silence all the notification/digest/run calls — we only care about offline logic
        patch("src.scheduler.scheduler.run_incremental", new_callable=AsyncMock, return_value={}),
        patch("src.scheduler.scheduler.run_full_resolution", new_callable=AsyncMock, return_value={}),
        patch("src.scheduler.scheduler.get_last_run_time", new_callable=AsyncMock, return_value=None),
        patch("src.scheduler.scheduler._build_status", new_callable=AsyncMock, return_value={}),
        patch("src.scheduler.scheduler.notify_status", new_callable=AsyncMock),
        patch("src.scheduler.scheduler._build_daily_digest", new_callable=AsyncMock, return_value={}),
        patch("src.scheduler.scheduler.notify_daily_digest", new_callable=AsyncMock),
        patch("src.scheduler.scheduler.build_identity_digest", new_callable=AsyncMock, return_value={}),
        patch("src.scheduler.scheduler.notify_identity_digest", new_callable=AsyncMock),
        patch("src.scheduler.scheduler._push_new_alerts", new_callable=AsyncMock),
        patch("src.scheduler.scheduler._check_collector_health", new_callable=AsyncMock, return_value=[]),
        patch("src.scheduler.scheduler.notify_collector_health", new_callable=AsyncMock),
        patch("src.scheduler.scheduler._check_merge_candidates", new_callable=AsyncMock),
        patch("src.scheduler.scheduler._run_db_backup_check", new_callable=AsyncMock),
        patch("src.scheduler.scheduler._run_decision_outbox_check", new_callable=AsyncMock),
        patch("src.scheduler.scheduler._stage_collector_priority_hints", new_callable=AsyncMock, return_value={}),
        patch("src.scheduler.scheduler._stage_identity_truth_and_indicators", new_callable=AsyncMock, return_value={}),
        # Speed up: make asyncio.sleep a no-op
        patch("src.scheduler.scheduler.asyncio.sleep", new_callable=AsyncMock),
    ]


async def _run_ticks(n_ticks: int, connectivity_fn):
    """Run start_scheduler for exactly n_ticks then stop it."""
    import contextlib
    import src.scheduler.scheduler as sched

    tick = 0

    async def _counting_sleep(seconds):
        nonlocal tick
        tick += 1
        if tick >= n_ticks:
            sched.stop_scheduler()

    patches = _base_scheduler_patches(connectivity_fn)
    patches.append(patch("src.scheduler.scheduler.asyncio.sleep", side_effect=_counting_sleep))

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        sched._running = True
        await sched.start_scheduler()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scheduler_logs_warning_on_first_offline_tick(caplog):
    """First tick with DB unreachable logs the 'pausing' warning exactly once."""
    # offline, offline, online (so we get two offline ticks then stop)
    connectivity = _make_connectivity_sequence(False, False, True)

    with caplog.at_level(logging.WARNING, logger="src.scheduler.scheduler"):
        await _run_ticks(3, connectivity)

    warning_lines = [r.message for r in caplog.records if "pausing" in r.message]
    assert len(warning_lines) == 1, (
        f"Expected exactly 1 'pausing' warning, got {len(warning_lines)}: {warning_lines}"
    )


@pytest.mark.asyncio
async def test_scheduler_does_not_repeat_warning_on_consecutive_offline_ticks(caplog):
    """Consecutive offline ticks must NOT re-log the 'pausing' warning."""
    connectivity = _make_connectivity_sequence(False, False, False, True)

    with caplog.at_level(logging.WARNING, logger="src.scheduler.scheduler"):
        await _run_ticks(4, connectivity)

    warning_lines = [r.message for r in caplog.records if "pausing" in r.message]
    assert len(warning_lines) == 1, (
        f"Warning logged {len(warning_lines)} times on consecutive offline ticks (expected 1)"
    )


@pytest.mark.asyncio
async def test_scheduler_logs_restored_on_reconnect(caplog):
    """After offline, the first online tick logs 'restored' exactly once."""
    connectivity = _make_connectivity_sequence(False, True)

    with caplog.at_level(logging.INFO, logger="src.scheduler.scheduler"):
        await _run_ticks(2, connectivity)

    restored_lines = [r.message for r in caplog.records if "restored" in r.message]
    assert len(restored_lines) == 1, (
        f"Expected exactly 1 'restored' log, got {len(restored_lines)}: {restored_lines}"
    )


@pytest.mark.asyncio
async def test_scheduler_was_offline_resets_after_reconnect(caplog):
    """was_offline must reset to False after reconnect so a second outage re-logs the warning."""
    # offline -> online -> offline again: should get TWO 'pausing' warnings
    connectivity = _make_connectivity_sequence(False, True, False, True)

    with caplog.at_level(logging.WARNING, logger="src.scheduler.scheduler"):
        await _run_ticks(4, connectivity)

    warning_lines = [r.message for r in caplog.records if "pausing" in r.message]
    assert len(warning_lines) == 2, (
        f"Expected 2 'pausing' warnings (two separate outages), got {len(warning_lines)}"
    )
