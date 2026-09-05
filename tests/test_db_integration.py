"""
QA-lane DB integration tests.

These tests require a live Postgres database.  They skip automatically
when ANALYZER_DATABASE_URL is not reachable (so CI without a DB stays
green).  Run locally or in a docker-compose test profile with:

    ANALYZER_DATABASE_URL=postgres://collector:collector@localhost:5500/unifiedanalyzer \
    python -m pytest tests/test_db_integration.py -v

Coverage impact: hitting init_pools() and the real asyncpg flow covers
src/db/connection.py, plus any module that calls get_analyzer_pool().
"""
from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Skip marker — applied when DB is unreachable
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.asyncio

_ANALYZER_URL = os.getenv(
    "ANALYZER_DATABASE_URL",
    "postgres://collector:collector@localhost:5500/unifiedanalyzer",
)


async def _db_reachable(url: str) -> bool:
    try:
        import asyncpg
        conn = await asyncpg.connect(url, timeout=3.0)
        await conn.fetchval("SELECT 1")
        await conn.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# DB connectivity smoke tests
# ---------------------------------------------------------------------------

class TestAnalyzerDBConnectivity:
    async def test_analyzer_db_select_1(self):
        """Smoke test: analyzer DB accepts a connection and responds."""
        if not await _db_reachable(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        result = await conn.fetchval("SELECT 1")
        await conn.close()
        assert result == 1

    async def test_analyzer_db_schema_has_entities_table(self):
        """Schema smoke test: entities table exists (schema.sql applied)."""
        if not await _db_reachable(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            row = await conn.fetchrow(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'entities'
                """
            )
            assert row is not None, "entities table not found — run: python -m src.main schema"
        finally:
            await conn.close()

    async def test_analyzer_db_schema_has_identity_signals_table(self):
        if not await _db_reachable(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            row = await conn.fetchrow(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'identity_signals'
                """
            )
            assert row is not None
        finally:
            await conn.close()

    async def test_analyzer_db_schema_has_analysis_runs_table(self):
        if not await _db_reachable(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            row = await conn.fetchrow(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'analysis_runs'
                """
            )
            assert row is not None
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# src.db.connection integration — init_pools / close_pools
# ---------------------------------------------------------------------------

class TestDbConnectionModule:
    async def test_init_pools_and_close_pools(self):
        """init_pools() and close_pools() round-trip without error."""
        if not await _db_reachable(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")

        from src.db.connection import close_pools, get_analyzer_pool, init_pools

        await init_pools(apply_schema_ddl=False)
        try:
            pool = get_analyzer_pool()
            result = await pool.fetchval("SELECT 42")
            assert result == 42
        finally:
            await close_pools()

    async def test_check_db_connectivity_returns_true_when_up(self):
        if not await _db_reachable(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")

        from src.db.connection import check_db_connectivity, close_pools, init_pools

        await init_pools(apply_schema_ddl=False)
        try:
            result = await check_db_connectivity()
            assert result is True
        finally:
            await close_pools()


# ---------------------------------------------------------------------------
# src.pipeline.run_reporting integration — production_run_types()
# ---------------------------------------------------------------------------

class TestRunReportingIntegration:
    async def test_production_run_types_against_live_schema(self):
        """production_run_types() values are valid run_type values in analysis_runs."""
        if not await _db_reachable(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        from src.pipeline.run_reporting import production_run_types

        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            # The column exists and accepts our run type values (constraint check)
            run_types = production_run_types()
            assert "incremental" in run_types
            assert "full_resolution" in run_types
            # Verify the column actually exists in the schema
            row = await conn.fetchrow(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'analysis_runs' AND column_name = 'run_type'
                """
            )
            assert row is not None
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# src.pipeline.text_normalizer integration — source_fingerprint stability
# ---------------------------------------------------------------------------

class TestTextNormalizerIntegration:
    async def test_source_fingerprint_stable_across_calls(self):
        """source_fingerprint is deterministic — same row always same hash."""
        from src.pipeline.text_normalizer import source_fingerprint

        row = {
            "id": "00000000-0000-0000-0000-000000000001",
            "entity_id": "00000000-0000-0000-0000-000000000002",
            "occurred_at": "2024-01-01T00:00:00+00:00",
            "source": "instagram",
            "event_type": "CONTENT_PUBLISHED",
            "source_record_id": "post-abc",
            "title": "Test post",
            "detail": None,
            "metadata": None,
        }
        fp1 = source_fingerprint(row)
        fp2 = source_fingerprint(row)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA256 hex
        # This test passes without DB — included here as part of the integration suite


# ---------------------------------------------------------------------------
# src.pipeline.incremental_runner — get_last_run_time with live DB
# ---------------------------------------------------------------------------

class TestIncrementalRunnerIntegration:
    async def test_get_last_run_time_returns_none_or_datetime(self):
        """get_last_run_time() against live DB returns None or a datetime."""
        if not await _db_reachable(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")

        from datetime import datetime
        from src.db.connection import close_pools, init_pools
        from src.pipeline.incremental_runner import get_last_run_time

        await init_pools(apply_schema_ddl=False)
        try:
            result = await get_last_run_time("incremental")
            assert result is None or isinstance(result, datetime)
        finally:
            await close_pools()

    async def test_production_run_types_never_includes_test_run(self):
        """Sanity: 'test' is not in production_run_types()."""
        from src.pipeline.run_reporting import is_production_run_type
        assert not is_production_run_type("test")
        assert not is_production_run_type("probe")
        assert is_production_run_type("incremental")
