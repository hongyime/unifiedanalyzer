"""
QA-lane DB integration tests for remaining low-coverage pipeline modules.

Targets (measured coverage without live DB):
  graph_overlap 23%, relationship_intelligence 26%, social_face_link 17%,
  strava_patterns 21%, group_graph 14%, recon_bridge 31%, bio_nlp 35%

All tests skip automatically when ANALYZER_DATABASE_URL is unreachable.
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
    from src.db.connection import close_pools, init_pools
    await init_pools(apply_schema_ddl=False)
    try:
        return await coro
    finally:
        await close_pools()


# ---------------------------------------------------------------------------
# graph_overlap (23% without DB)
# ---------------------------------------------------------------------------

class TestGraphOverlapIntegration:
    async def test_compute_graph_overlap_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.graph_overlap import compute_graph_overlap
        result = await _with_pools(compute_graph_overlap())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# relationship_intelligence (26% without DB)
# ---------------------------------------------------------------------------

class TestRelationshipIntelligenceIntegration:
    async def test_refresh_relationship_intelligence_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.relationship_intelligence import refresh_relationship_intelligence
        result = await _with_pools(refresh_relationship_intelligence())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# social_face_link (17% without DB)
# ---------------------------------------------------------------------------

class TestSocialFaceLinkIntegration:
    async def test_emit_social_face_link_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.social_face_link import emit_social_face_link_signals
        result = await _with_pools(emit_social_face_link_signals())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# strava_patterns (21% without DB)
# ---------------------------------------------------------------------------

class TestStravaPatternsIntegration:
    async def test_analyze_strava_patterns_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Analyzer or Collector DB not reachable")
        from src.pipeline.strava_patterns import analyze_strava_patterns
        result = await _with_pools(analyze_strava_patterns())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# group_graph (14% without DB)
# ---------------------------------------------------------------------------

class TestGroupGraphIntegration:
    async def test_build_whatsapp_group_graph_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Analyzer or Collector DB not reachable")
        from src.pipeline.group_graph import build_whatsapp_group_graph
        result = await _with_pools(build_whatsapp_group_graph())
        assert isinstance(result, dict)

    async def test_build_telegram_group_graph_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Analyzer or Collector DB not reachable")
        from src.pipeline.group_graph import build_telegram_group_graph
        result = await _with_pools(build_telegram_group_graph())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# bio_nlp (35% without DB)
# ---------------------------------------------------------------------------

class TestBioNlpIntegration:
    async def test_analyze_bios_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Analyzer or Collector DB not reachable")
        from src.pipeline.bio_nlp import analyze_bios
        result = await _with_pools(analyze_bios())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# recon_bridge (31% without DB)
# ---------------------------------------------------------------------------

class TestReconBridgeIntegration:
    async def test_bridge_recon_observations_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Analyzer or Collector DB not reachable")
        from src.pipeline.recon_bridge import bridge_recon_observations
        result = await _with_pools(
            bridge_recon_observations(dry_run=True, limit=10)
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# contact_extraction (28% without DB)
# ---------------------------------------------------------------------------

class TestContactExtractionIntegration:
    async def test_extract_contacts_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Analyzer or Collector DB not reachable")
        from src.pipeline.contact_extraction import extract_contacts
        result = await _with_pools(extract_contacts())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# cross_source_signals (23% without DB)
# ---------------------------------------------------------------------------

class TestCrossSourceSignalsIntegration:
    async def test_emit_cross_source_signals_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Analyzer or Collector DB not reachable")
        from src.pipeline.cross_source_signals import emit_cross_source_signals
        result = await _with_pools(emit_cross_source_signals())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# location_inference (34% without DB)
# ---------------------------------------------------------------------------

class TestLocationInferenceIntegration:
    async def test_infer_locations_returns_dict(self):
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip("Analyzer or Collector DB not reachable")
        from src.pipeline.location_inference import infer_locations
        result = await _with_pools(infer_locations())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# behavioral_profiler (22% without DB)
# ---------------------------------------------------------------------------

class TestBehavioralProfilerIntegration:
    async def test_compute_behavioral_profiles_returns_int(self):
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip("Analyzer DB not reachable")
        from src.pipeline.behavioral_profiler import compute_behavioral_profiles
        result = await _with_pools(compute_behavioral_profiles())
        assert isinstance(result, int)
        assert result >= 0
