"""
QA-lane heavy DB integration tests — batch 2. Require PYTEST_INTEGRATION_DB_ONLY=1.

Covers remaining high-value async pipeline entry points:
- cross_source_signals.emit_cross_source_signals
- entity_enrichment.enrich_entities_with_ner
- location_inference.infer_locations
- shared_life_context.emit_shared_life_context_signals
- recon_bridge.bridge_recon_observations
- group_graph.build_whatsapp_group_graph + build_telegram_group_graph

All skip locally, run in CI against empty postgres:16.
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
_SKIP_BOTH = "Analyzer or Collector DB not reachable"


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
# cross_source_signals.emit_cross_source_signals
# ---------------------------------------------------------------------------

class TestCrossSourceSignalsIntegration:
    async def test_emit_cross_source_signals_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip(_SKIP_BOTH)
        from src.pipeline.cross_source_signals import emit_cross_source_signals
        result = await _with_pools(emit_cross_source_signals())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# entity_enrichment.enrich_entities_with_ner
# ---------------------------------------------------------------------------

class TestEntityEnrichmentIntegration:
    async def test_enrich_entities_with_ner_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip(_SKIP_ANALYZER)
        from src.pipeline.entity_enrichment import enrich_entities_with_ner
        result = await _with_pools(enrich_entities_with_ner())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# location_inference.infer_locations
# ---------------------------------------------------------------------------

class TestLocationInferenceIntegration:
    async def test_infer_locations_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip(_SKIP_BOTH)
        from src.pipeline.location_inference import infer_locations
        result = await _with_pools(infer_locations())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# shared_life_context.emit_shared_life_context_signals
# ---------------------------------------------------------------------------

class TestSharedLifeContextIntegration:
    async def test_emit_shared_life_context_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL):
            pytest.skip(_SKIP_ANALYZER)
        from src.pipeline.shared_life_context import emit_shared_life_context_signals
        result = await _with_pools(emit_shared_life_context_signals())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# recon_bridge.bridge_recon_observations (dry_run)
# ---------------------------------------------------------------------------

class TestReconBridgeIntegration:
    async def test_bridge_recon_observations_dry_run(self):
        """dry_run=True reads without writing — safe on real or empty DB."""
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip(_SKIP_BOTH)
        from src.pipeline.recon_bridge import bridge_recon_observations
        result = await _with_pools(bridge_recon_observations(dry_run=True, limit=50))
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# group_graph.build_whatsapp_group_graph + build_telegram_group_graph
# ---------------------------------------------------------------------------

class TestGroupGraphIntegration:
    async def test_build_whatsapp_group_graph_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip(_SKIP_BOTH)
        from src.pipeline.group_graph import build_whatsapp_group_graph
        result = await _with_pools(build_whatsapp_group_graph())
        assert isinstance(result, dict)

    async def test_build_telegram_group_graph_returns_dict(self):
        if not _INTEGRATION_ENABLED:
            pytest.skip(_SKIP_HEAVY)
        if not await _db_ok(_ANALYZER_URL) or not await _db_ok(_COLLECTOR_URL):
            pytest.skip(_SKIP_BOTH)
        from src.pipeline.group_graph import build_telegram_group_graph
        result = await _with_pools(build_telegram_group_graph())
        assert isinstance(result, dict)
