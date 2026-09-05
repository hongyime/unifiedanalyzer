"""
QA-lane test: _run_phase must re-raise is_db_transient_error exceptions
instead of swallowing them as a 'failed' phase status.

Currently _run_phase only checks is_collector_unavailable_error and marks
everything else as failed (non-fatal). A transient analyzer-DB error (e.g.
Postgres in recovery) should bubble up so run_incremental's outer except
can suppress the Telegram alert via is_db_transient_error().

RED: before dev adds the re-raise — _run_phase returns without raising.
GREEN: after dev adds `if is_db_transient_error(e): raise` inside _run_phase.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from src.pipeline.incremental_runner import _run_phase


_RUN_ID = "00000000-0000-0000-0000-000000000001"
_RUN_TYPE = "incremental"


def _transient():
    return asyncpg.PostgresError("the database system is in recovery mode")


def _nontransient():
    return RuntimeError("logic bug in phase")


def _collector_unavailable():
    from src.db.connection import CollectorUnavailableError
    return CollectorUnavailableError("collector pool not initialized")


# ---------------------------------------------------------------------------
# Core contract: transient errors must propagate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_phase_reraises_transient_db_error():
    """_run_phase must NOT swallow a transient analyzer-DB error."""
    with (
        patch(
            "src.pipeline.incremental_runner.get_analyzer_pool",
            side_effect=RuntimeError("pool unavailable"),   # status write will fail too
        ),
    ):
        with pytest.raises(asyncpg.PostgresError, match="recovery mode"):
            await _run_phase(
                _RUN_ID, _RUN_TYPE, "test_phase",
                AsyncMock(side_effect=_transient()),
            )


@pytest.mark.asyncio
async def test_run_phase_does_not_reraise_nontransient_error():
    """_run_phase must still swallow non-transient phase errors (existing behaviour)."""
    with (
        patch(
            "src.pipeline.incremental_runner.get_analyzer_pool",
            side_effect=RuntimeError("pool unavailable"),
        ),
    ):
        # Should not raise — returns default (None)
        result = await _run_phase(
            _RUN_ID, _RUN_TYPE, "test_phase",
            AsyncMock(side_effect=_nontransient()),
        )
    assert result is None


@pytest.mark.asyncio
async def test_run_phase_still_skips_collector_unavailable():
    """Collector-unavailable errors must still be marked skipped, not re-raised."""
    with (
        patch(
            "src.pipeline.incremental_runner.get_analyzer_pool",
            side_effect=RuntimeError("pool unavailable"),
        ),
    ):
        result = await _run_phase(
            _RUN_ID, _RUN_TYPE, "test_phase",
            AsyncMock(side_effect=_collector_unavailable()),
        )
    # Returns skipped dict, does not raise
    assert isinstance(result, dict)
    assert result.get("skipped") == "collector_unavailable"
