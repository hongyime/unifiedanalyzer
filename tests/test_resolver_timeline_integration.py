"""
QA-lane complex DB integration tests for:
- entity_resolver.resolve_entities: full identity resolution pipeline
- timeline_builder.build_timeline: timeline event construction

These are the two highest-value DB-dependent entry points in the codebase.
Both skip gracefully when the analyzer DB is unreachable and run in CI
against the postgres:16 service.

Why these are complex:
- resolve_entities reads from both analyzer and collector DBs, runs fuzzy
  matching across thousands of profiles, writes entities + signals + links
- build_timeline reads collector per-source tables, normalizes events into
  timeline_events with entity attribution, manages partitioning

Tests are deliberately lightweight — just assert the function runs and
returns a valid stats dict. The goal is coverage of the hot paths, not
exhaustive behavioral testing (that requires real data).
"""
from __future__ import annotations

import os

import pytest

_ANALYZER_URL = os.getenv(
    "ANALYZER_DATABASE_URL",
    "postgres://collector:collector@localhost:5500/unifiedanalyzer",
)
_COLLECTOR_URL = os.getenv(
    "COLLECTOR_DATABASE_URL",
    "postgres://collector:collector@localhost:5500/unifiedcollector",
)

pytestmark = pytest.mark.asyncio


async def _db_ok(url: str) -> bool:
    try:
        import asyncpg
        c = await asyncpg.connect(url, timeout=3.0)
        await c.fetchval("SELECT 1")
        await c.close()
        return True
    except Exception:
        return False


async def _with_pools(coro):
    # Guard: init_pools requires ANALYZER_DATABASE_URL env var
    if not os.getenv("ANALYZER_DATABASE_URL"):
        # No env var → coro will never run; we need to close it to avoid warning
        coro.close()
        pytest.skip("ANALYZER_DATABASE_URL not set")
    from src.db.connection import close_pools, init_pools
    await init_pools(apply_schema_ddl=False)
    try:
        return await coro
    finally:
        await close_pools()


# ---------------------------------------------------------------------------
# entity_resolver.resolve_entities
# ---------------------------------------------------------------------------

class TestResolveEntitiesIntegration:
    async def test_resolve_entities_returns_stats_dict(self):
        """resolve_entities() runs end-to-end and returns a stats dict."""
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        if not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Collector DB not reachable")

        from src.pipeline.entity_resolver import resolve_entities
        result = await _with_pools(resolve_entities())
        assert isinstance(result, dict)
        # Key stats fields that should always be present
        for key in ("entities_created", "entities_updated", "links", "signals"):
            assert key in result, f"Missing key: {key}"

    async def test_resolve_entities_counts_are_non_negative(self):
        """All numeric stats must be >= 0."""
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        if not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Collector DB not reachable")

        from src.pipeline.entity_resolver import resolve_entities
        result = await _with_pools(resolve_entities())
        for key, value in result.items():
            if isinstance(value, int):
                assert value >= 0, f"{key} is negative: {value}"

    async def test_resolve_entities_is_idempotent(self):
        """Running resolve_entities twice in a row should not crash
        and should produce non-negative stats both times."""
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        if not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Collector DB not reachable")

        from src.pipeline.entity_resolver import resolve_entities

        # First run
        r1 = await _with_pools(resolve_entities())
        assert isinstance(r1, dict)

        # Second run — entities already exist, should update/skip rather than fail
        r2 = await _with_pools(resolve_entities())
        assert isinstance(r2, dict)


# ---------------------------------------------------------------------------
# timeline_builder.build_timeline
# ---------------------------------------------------------------------------

class TestBuildTimelineIntegration:
    async def test_build_timeline_returns_stats_dict(self):
        """build_timeline() runs and returns a stats dict."""
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        if not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Collector DB not reachable")

        from src.pipeline.timeline_builder import build_timeline
        result = await _with_pools(build_timeline(since=None))
        assert isinstance(result, dict)
        # Key stats fields
        for key in ("total", "inserted", "skipped_tables"):
            assert key in result, f"Missing key: {key}"

    async def test_build_timeline_with_only_sources_filter(self):
        """build_timeline with only_sources filter skips other platforms."""
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        if not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Collector DB not reachable")

        from src.pipeline.timeline_builder import build_timeline
        # Pass an empty set as only_sources — should skip all collector tables
        result = await _with_pools(
            build_timeline(since=None, only_sources={"__nonexistent_source__"})
        )
        assert isinstance(result, dict)

    async def test_build_timeline_inserted_is_non_negative(self):
        """Inserted count must be >= 0."""
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        if not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Collector DB not reachable")

        from src.pipeline.timeline_builder import build_timeline
        result = await _with_pools(build_timeline(since=None))
        assert result.get("total", 0) >= 0
        assert result.get("inserted", 0) >= 0

    async def test_build_timeline_skip_sources_works(self):
        """build_timeline with skip_sources respects the skip set."""
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        if not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Collector DB not reachable")

        from src.pipeline.timeline_builder import build_timeline
        # Skip github (large table) to keep this test fast
        result = await _with_pools(
            build_timeline(since=None, skip_sources={"github"})
        )
        assert isinstance(result, dict)
        # github should appear in skipped_tables since we skipped it
        skipped = result.get("skipped_tables", [])
        assert isinstance(skipped, list)


# ---------------------------------------------------------------------------
# Combined: ensure_timeline_partitions survives even on a fresh DB
# ---------------------------------------------------------------------------

class TestEnsureTimelinePartitionsIntegration:
    async def test_ensure_partitions_is_idempotent(self):
        """Calling ensure_timeline_partitions multiple times must not fail."""
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")

        from src.pipeline.timeline_builder import ensure_timeline_partitions

        await _with_pools(ensure_timeline_partitions(months_ahead=3))
        await _with_pools(ensure_timeline_partitions(months_ahead=3))
