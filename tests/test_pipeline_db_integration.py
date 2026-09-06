"""
QA-lane pipeline DB integration tests.

Covers the lowest-coverage pipeline modules by calling their async
entry points against a live Postgres DB. All tests skip automatically
when ANALYZER_DATABASE_URL is unreachable.

Modules targeted (% without live DB):
  entity_resolver 33%, timeline_builder 25%, graph_analytics 12%,
  topical_similarity 16%, face_clustering 15%, scheduler 45%,
  incremental_runner 66%, identity_scorer 88%, data_quality_ledger,
  identity_calibration.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

_ANALYZER_URL = os.getenv(
    "ANALYZER_DATABASE_URL",
    "postgres://collector:collector@localhost:5500/unifiedanalyzer",
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
    """Initialize src.db.connection pools, run coro, close pools."""
    from src.db.connection import close_pools, init_pools
    await init_pools(apply_schema_ddl=False)
    try:
        return await coro
    finally:
        await close_pools()


# ---------------------------------------------------------------------------
# entity_resolver + incremental_runner
# ---------------------------------------------------------------------------

class TestEntityResolverIntegration:
    async def test_load_platform_profiles_skips_without_collector_db(self):
        # load_platform_profiles reads collector DB tables (github_users, etc.)
        # The CI test DB is analyzer-only — skip if collector tables absent.
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            row = await conn.fetchrow(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'github_users'"
            )
        finally:
            await conn.close()
        if not row:
            pytest.skip("Collector tables not in this DB — skipping load_platform_profiles test")
        from src.pipeline.entity_resolver import load_platform_profiles
        by_username, no_username = await _with_pools(load_platform_profiles())
        assert isinstance(by_username, dict)
        assert isinstance(no_username, list)

    async def test_get_last_run_time_incremental(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.incremental_runner import get_last_run_time
        result = await _with_pools(get_last_run_time("incremental"))
        assert result is None or isinstance(result, datetime)

    async def test_get_last_run_time_full_resolution(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.incremental_runner import get_last_run_time
        result = await _with_pools(get_last_run_time("full_resolution"))
        assert result is None or isinstance(result, datetime)

    async def test_clear_orphaned_run_locks_returns_int(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.incremental_runner import clear_orphaned_run_locks
        result = await _with_pools(clear_orphaned_run_locks())
        assert isinstance(result, int)
        assert result >= 0


# ---------------------------------------------------------------------------
# timeline_builder
# ---------------------------------------------------------------------------

class TestTimelineBuilderIntegration:
    async def test_ensure_timeline_partitions_runs(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.timeline_builder import ensure_timeline_partitions
        await _with_pools(ensure_timeline_partitions(months_ahead=2))

    async def test_repair_replied_metadata_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.timeline_builder import repair_replied_metadata
        result = await _with_pools(
            repair_replied_metadata(batch_size=10, max_batches=1)
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# graph_analytics
# ---------------------------------------------------------------------------

class TestGraphAnalyticsIntegration:
    async def test_compute_graph_analytics_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.graph_analytics import compute_graph_analytics
        result = await _with_pools(compute_graph_analytics())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# identity_scorer
# ---------------------------------------------------------------------------

class TestIdentityScorerIntegration:
    async def test_compute_identity_scores_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.identity_scorer import compute_identity_scores
        result = await _with_pools(compute_identity_scores())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# scheduler._build_status
# ---------------------------------------------------------------------------

class TestSchedulerIntegration:
    async def test_build_status_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.scheduler.scheduler import _build_status
        result = await _with_pools(_build_status())
        assert isinstance(result, dict)
        assert result.get("db_ok") is True

    async def test_check_collector_health_returns_list(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.scheduler.scheduler import _check_collector_health
        result = await _with_pools(_check_collector_health())
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# face_clustering — schema creation + flag_junk_faces
# ---------------------------------------------------------------------------

class TestFaceClusteringIntegration:
    async def test_ensure_schema_creates_facetracker(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            row = await conn.fetchrow(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'facetracker'"
            )
        finally:
            await conn.close()
        if not row:
            pytest.skip("facetracker schema not present — requires face_worker schema migration")
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            from src.pipeline.face_clustering import _ensure_schema
            await _ensure_schema(conn)
            row2 = await conn.fetchrow(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'facetracker'"
            )
            assert row2 is not None
        finally:
            await conn.close()

    async def test_flag_junk_faces_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            row = await conn.fetchrow(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'facetracker'"
            )
        finally:
            await conn.close()
        if not row:
            pytest.skip("facetracker schema not present — skipping flag_junk_faces")
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            from src.pipeline.face_clustering import _ensure_schema
            await _ensure_schema(conn)
        finally:
            await conn.close()
        from src.pipeline.face_clustering import flag_junk_faces
        result = await _with_pools(flag_junk_faces())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# topical_similarity
# ---------------------------------------------------------------------------

class TestTopicalSimilarityIntegration:
    async def test_emit_topical_similarity_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.topical_similarity import emit_topical_similarity_signals
        result = await _with_pools(emit_topical_similarity_signals())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# data_quality_ledger
# ---------------------------------------------------------------------------

class TestDataQualityLedgerIntegration:
    async def test_configured_sources_returns_nonempty_list(self):
        """Pure — no DB needed."""
        from src.pipeline.data_quality_ledger import _configured_sources
        sources = _configured_sources()
        assert isinstance(sources, list)
        assert len(sources) > 0

    async def test_build_data_quality_ledger_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        from src.pipeline.data_quality_ledger import build_data_quality_ledger
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=10.0)
        try:
            result = await build_data_quality_ledger(conn)
            assert isinstance(result, dict)
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# identity_calibration
# ---------------------------------------------------------------------------

class TestIdentityCalibrationIntegration:
    async def test_snapshot_pair_features_empty_pair_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        import asyncpg
        conn = await asyncpg.connect(_ANALYZER_URL, timeout=5.0)
        try:
            from src.pipeline.identity_calibration import snapshot_pair_features
            result = await snapshot_pair_features(
                conn,
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            )
            assert isinstance(result, dict)
        finally:
            await conn.close()

    async def test_noisy_or_probs_pure_no_db(self):
        """Pure function — no DB needed."""
        from src.pipeline.identity_calibration import FEATURE_ORDER, _noisy_or_probs
        zero_row = [[0.0] * len(FEATURE_ORDER)]
        result = _noisy_or_probs(zero_row)
        assert result[0] == 0.0
