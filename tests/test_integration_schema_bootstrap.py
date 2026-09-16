"""Exercise schema replay with synthetic records in the guarded CI databases."""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest

from scripts.bootstrap_integration_db import apply_face_schema, apply_schemas, validate_test_urls


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("PYTEST_INTEGRATION_DB_ONLY") != "1", reason="Requires disposable CI databases")
async def test_schema_replay_preserves_records_and_database_boundaries() -> None:
    analyzer_url, collector_url = validate_test_urls(dict(os.environ, ANALYZER_CI_SCHEMA_SETUP="1"))
    analyzer = await asyncpg.connect(analyzer_url, timeout=5, command_timeout=30)
    collector = await asyncpg.connect(collector_url, timeout=5, command_timeout=30)
    entity_id = image_id = media_id = None
    marker = str(uuid4())
    try:
        assert await analyzer.fetchval("SELECT current_database()") == "analyzer_ci"
        assert await collector.fetchval("SELECT current_database()") == "collector_ci"
        assert await analyzer.fetchval("SELECT to_regclass('public.collection_targets')") is None
        assert await collector.fetchval("SELECT to_regclass('public.entities')") is None

        entity_id = await analyzer.fetchval(
            "INSERT INTO entities(canonical_name,metadata) VALUES ($1, '{\"preserved\":true}') RETURNING id",
            marker,
        )
        image_id = await analyzer.fetchval(
            "INSERT INTO facetracker.images(file_path,file_hash,file_size,file_mtime) "
            "VALUES ($1,$2,17,0) RETURNING id", marker, marker,
        )
        media_id = await collector.fetchval(
            "INSERT INTO media_items(source,entity_id,entity_name,content_type,content_id,filename,"
            "file_path,file_size,metadata,source_url) "
            "VALUES ('fixture',$1,'Synthetic fixture','photo',$2,'fixture.jpg',$3,17,"
            "'{\"preserved\":true}','https://example.invalid/fixture') RETURNING id", marker, marker, marker,
        )
        queries = [
            (analyzer, "SELECT row_to_json(e)::text FROM entities e WHERE id=$1", entity_id),
            (analyzer, "SELECT row_to_json(i)::text FROM facetracker.images i WHERE id=$1", image_id),
            (collector, "SELECT row_to_json(m)::text FROM media_items m WHERE id=$1", media_id),
        ]
        before = [await conn.fetchval(query, record_id) for conn, query, record_id in queries]
        ledger_query = "SELECT filename,checksum,applied_at FROM schema_migrations ORDER BY filename"
        ledger_before = await collector.fetch(ledger_query)
        assert ledger_before

        summary = await asyncio.wait_for(apply_schemas(analyzer_url, collector_url), timeout=120)
        apply_face_schema(analyzer_url)

        assert summary["deferred"] is False
        assert summary["migrations_applied"] == []
        assert [await conn.fetchval(query, record_id) for conn, query, record_id in queries] == before
        assert await collector.fetch(ledger_query) == ledger_before
    finally:
        # Remove only these synthetic rows so other integration cases start clean.
        if media_id is not None:
            await collector.execute("DELETE FROM media_items WHERE id=$1", media_id)
        if image_id is not None:
            await analyzer.execute("DELETE FROM facetracker.images WHERE id=$1", image_id)
        if entity_id is not None:
            await analyzer.execute("DELETE FROM entities WHERE id=$1", entity_id)
        await collector.close()
        await analyzer.close()
