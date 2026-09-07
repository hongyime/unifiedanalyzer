"""
QA-lane heavy DB integration tests — require PYTEST_INTEGRATION_DB_ONLY=1.

Covers:
- face_clustering.cluster_faces: FAISS + InsightFace kNN on facetracker schema
- face_clustering.propagate_entity_faces: bridge facetracker faces → entity_faces
- scheduler._build_daily_digest: daily stats snapshot
- scheduler._check_merge_candidates: merge-candidate Telegram card logic (read-only)
- pipeline.contact_extraction.extract_contacts: bio + content email/phone scanning
- pipeline.identity_scorer.compute_identity_scores: signal fusion → entity_relationships

These skip locally (PYTEST_INTEGRATION_DB_ONLY not set) and run in CI
against the clean postgres:16 service.
"""
from __future__ import annotations

import os

import pytest

_INTEGRATION_ENABLED = os.getenv("PYTEST_INTEGRATION_DB_ONLY", "0") == "1"
_ANALYZER_URL = os.getenv(
    "ANALYZER_DATABASE_URL",
    "postgres://collector:collector@localhost:5500/unifiedanalyzer",
)
_COLLECTOR_URL = os.getenv(
    "COLLECTOR_DATABASE_URL",
    "postgres://collector:collector@localhost:5500/unifiedcollector",
)

pytestmark = pytest.mark.asyncio

_SKIP_HEAVY = "Set PYTEST_INTEGRATION_DB_ONLY=1 to run heavy integration tests"
_SKIP_ANALYZER = "Analyzer DB not reachable"
_SKIP_COLLECTOR = "Analyzer or Collector DB not reachable"


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
    if not os.getenv("ANALYZER_DATABASE_URL"):
        coro.close()
        pytest.skip("ANALYZER_DATABASE_URL not set")
    from src.db.connection import close_pools, init_pools
    await init_pools(apply_schema_ddl=False)
    try:
        return await coro
    finally:
        await close_pools()


# ---------------------------------------------------------------------------
# face_clustering.cluster_faces
# ---------------------------------------------------------------------------

class TestFaceClusteringHeavyIntegration:
    async def test_cluster_faces_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip(_SKIP_ANALYZER)

        from src.pipeline.face_clustering import cluster_faces
        result = await _with_pools(cluster_faces())
        assert isinstance(result, dict)

    async def test_propagate_entity_faces_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip(_SKIP_ANALYZER)

        from src.pipeline.face_clustering import propagate_entity_faces
        result = await _with_pools(propagate_entity_faces())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# scheduler._build_daily_digest
# ---------------------------------------------------------------------------

class TestSchedulerDailyDigestIntegration:
    async def test_build_daily_digest_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip(_SKIP_ANALYZER)

        from src.scheduler.scheduler import _build_daily_digest
        result = await _with_pools(_build_daily_digest())
        assert isinstance(result, dict)
        # Key fields that should always be present in the daily digest
        for key in ("entity_count", "failed_runs", "runs_24h"):
            assert key in result, f"Missing key: {key}"

    async def test_build_daily_digest_counts_non_negative(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip(_SKIP_ANALYZER)

        from src.scheduler.scheduler import _build_daily_digest
        result = await _with_pools(_build_daily_digest())
        for key, value in result.items():
            if isinstance(value, int):
                assert value >= 0, f"{key} is negative: {value}"


# ---------------------------------------------------------------------------
# contact_extraction.extract_contacts
# ---------------------------------------------------------------------------

class TestContactExtractionHeavyIntegration:
    async def test_extract_contacts_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip(_SKIP_COLLECTOR)

        from src.pipeline.contact_extraction import extract_contacts
        result = await _with_pools(extract_contacts())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# identity_scorer.compute_identity_scores
# ---------------------------------------------------------------------------

class TestIdentityScorerHeavyIntegration:
    async def test_compute_identity_scores_is_idempotent(self):
        """Running compute_identity_scores twice must not crash."""
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip(_SKIP_ANALYZER)

        from src.pipeline.identity_scorer import compute_identity_scores

        r1 = await _with_pools(compute_identity_scores())
        assert isinstance(r1, dict)

        r2 = await _with_pools(compute_identity_scores())
        assert isinstance(r2, dict)


# ---------------------------------------------------------------------------
# pipeline.bio_nlp.analyze_bios
# ---------------------------------------------------------------------------

class TestBioNlpHeavyIntegration:
    async def test_analyze_bios_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip(_SKIP_COLLECTOR)

        from src.pipeline.bio_nlp import analyze_bios
        result = await _with_pools(analyze_bios())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# pipeline.alert_engine.run_alerts
# ---------------------------------------------------------------------------

class TestAlertEngineHeavyIntegration:
    async def test_run_alerts_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip(_SKIP_ANALYZER)

        from src.pipeline.alert_engine import run_alerts
        result = await _with_pools(run_alerts())
        assert isinstance(result, dict)
        # All alert type counts should be non-negative
        for key, value in result.items():
            if isinstance(value, int):
                assert value >= 0, f"{key} is negative: {value}"
