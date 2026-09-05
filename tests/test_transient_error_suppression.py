"""
QA-lane tests: run_incremental and run_full_resolution suppress notify_error
for transient DB errors and fire it for non-transient errors.

Injection point: get_last_run_time is the first awaitable inside the try block
of both runners — patching it to raise puts the exception exactly where the
is_db_transient_error guard lives.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _transient():
    return asyncpg.PostgresError("the database system is in recovery mode")


def _nontransient():
    return RuntimeError("logic bug: unexpected None in phase result")


_MOCK_RUN_ID = "test-run-id"


def _base_patches(exc):
    """Patches for run_incremental — injects via get_last_run_time (first try-block await)."""
    return [
        patch(
            "src.pipeline.incremental_runner._try_create_run",
            new_callable=AsyncMock,
            return_value=_MOCK_RUN_ID,
        ),
        patch(
            "src.pipeline.incremental_runner.get_last_run_time",
            new_callable=AsyncMock,
            side_effect=exc,
        ),
        patch(
            "src.pipeline.incremental_runner._finish_run",
            new_callable=AsyncMock,
        ),
        patch(
            "src.pipeline.incremental_runner._stop_heartbeat",
            new_callable=AsyncMock,
        ),
        patch(
            "src.pipeline.incremental_runner._heartbeat_loop",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ]


def _full_res_patches(exc):
    """Patches for run_full_resolution — inject via get_analyzer_pool so all pool
    calls raise, regardless of which phase hits it first."""
    return [
        patch(
            "src.pipeline.incremental_runner._try_create_run",
            new_callable=AsyncMock,
            return_value=_MOCK_RUN_ID,
        ),
        patch(
            "src.pipeline.incremental_runner.get_analyzer_pool",
            side_effect=exc,
        ),
        patch(
            "src.pipeline.incremental_runner._finish_run",
            new_callable=AsyncMock,
        ),
        patch(
            "src.pipeline.incremental_runner._stop_heartbeat",
            new_callable=AsyncMock,
        ),
        patch(
            "src.pipeline.incremental_runner._heartbeat_loop",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.pipeline.incremental_runner._alert_on_repeated_phase_failures",
            new_callable=AsyncMock,
        ),
    ]


# ---------------------------------------------------------------------------
# run_incremental
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_incremental_suppresses_notify_error_for_transient():
    """Transient DB error must NOT reach notify_error."""
    exc = _transient()
    patches = _base_patches(exc)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch(
            "src.pipeline.incremental_runner.notify_error",
            new_callable=AsyncMock,
        ) as mock_notify,
    ):
        from src.pipeline.incremental_runner import run_incremental
        with pytest.raises(asyncpg.PostgresError):
            await run_incremental()
    mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_run_incremental_fires_notify_error_for_nontransient():
    """Non-transient error MUST call notify_error with run-type 'incremental'."""
    exc = _nontransient()
    patches = _base_patches(exc)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch(
            "src.pipeline.incremental_runner.notify_error",
            new_callable=AsyncMock,
        ) as mock_notify,
    ):
        from src.pipeline.incremental_runner import run_incremental
        with pytest.raises(RuntimeError):
            await run_incremental()
    mock_notify.assert_called_once()
    args = mock_notify.call_args[0]
    assert args[0] == "incremental"
    assert "logic bug" in args[1]


# ---------------------------------------------------------------------------
# run_full_resolution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_full_resolution_suppresses_notify_error_for_transient():
    """Transient DB error in full_resolution must NOT reach notify_error."""
    exc = _transient()
    patches = _full_res_patches(exc)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch(
            "src.pipeline.incremental_runner.notify_error",
            new_callable=AsyncMock,
        ) as mock_notify,
    ):
        from src.pipeline.incremental_runner import run_full_resolution
        with pytest.raises(asyncpg.PostgresError):
            await run_full_resolution()
    mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_run_full_resolution_fires_notify_error_for_nontransient():
    """Non-transient error in full_resolution MUST call notify_error."""
    exc = _nontransient()
    patches = _full_res_patches(exc)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch(
            "src.pipeline.incremental_runner.notify_error",
            new_callable=AsyncMock,
        ) as mock_notify,
    ):
        from src.pipeline.incremental_runner import run_full_resolution
        with pytest.raises(RuntimeError):
            await run_full_resolution()
    mock_notify.assert_called_once()
    args = mock_notify.call_args[0]
    assert args[0] == "full resolution"
    assert "logic bug" in args[1]
