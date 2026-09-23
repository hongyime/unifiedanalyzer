from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from src.pipeline import graph_nl_query as nl


@pytest.mark.asyncio
async def test_context_maps_existing_platform_and_relationship_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given records shaped like the committed and deployed database schema.
    seen = datetime(2026, 9, 19, tzinfo=timezone.utc)
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={
        "id": "entity-a", "canonical_name": "Synthetic example",
        "created_at": seen, "last_seen_at": seen,
    })
    conn.fetch = AsyncMock(side_effect=[
        [{"source": "example", "platform_username": "fixture", "platform_id": "42", "updated_at": seen}],
        [{"entity_a_id": "entity-a", "entity_b_id": "entity-b", "relationship_type": "interaction",
          "cross_platform": False, "weight": 3, "sources": {"by_type": {"reply": 3}}, "last_seen_at": seen}],
        [{"source": "example", "event_type": "CONTENT_PUBLISHED", "occurred_at": seen, "title": "Fixture"}],
    ])
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(nl, "get_analyzer_pool", lambda: pool)

    # When building the local inference dossier.
    context = await nl._extract_context("entity-a")

    # Then real source identifiers and graph confidence survive the mapping.
    assert context["platforms"] == [{
        "platform": "example", "username": "fixture", "platform_user_id": "42", "updated_at": seen.isoformat(),
    }]
    assert context["edges"][0]["from_id"] == "entity-a"
    assert context["edges"][0]["to_id"] == "entity-b"
    assert context["edges"][0]["confidence"] == "weak"
    assert context["edges"][0]["source"] == "entity_relationships"


@pytest.mark.asyncio
async def test_database_failure_returns_explicit_context_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a failed database boundary with local NL enabled.
    monkeypatch.setenv("GRAPH_NL_ENABLED", "1")
    monkeypatch.setattr(nl, "_extract_context", AsyncMock(side_effect=asyncpg.PostgresError("unavailable")))
    backend = AsyncMock()
    monkeypatch.setattr(nl, "_call_backend", backend)

    # When a summary is requested.
    result = await nl.summarise_entity("entity-a")

    # Then unavailable evidence is reported without claiming an inference result.
    assert result["skipped"] == "context_unavailable"
    backend.assert_not_awaited()
