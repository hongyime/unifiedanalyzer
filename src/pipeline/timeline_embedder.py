"""Axis-1 MVP: pipeline phase that populates timeline_embeddings.

Reads timeline_events rows that don't yet have a matching row in
timeline_embeddings, batch-embeds their `title` text via text_embedder,
and upserts vectors. Re-embed on text change is handled via the ON CONFLICT
DO UPDATE ... WHERE text_sha1 <> EXCLUDED.text_sha1 predicate, so a run
that sees the same title twice does zero write work.

Registered as the `content_embedding` phase in incremental_runner, running
AFTER auto_label_seed and BEFORE identity_scoring so `topical_similarity`
(which reads timeline_embeddings) sees the freshest vectors.

Failure semantics (per spec): a failed model load — most likely on a
first-run system with no internet — must NOT propagate. Secondary phases
are non-fatal; we catch broadly and return a "skipped" stats row so
run_phase_status logs it, and the rest of the pipeline continues.

Cost bound: TEXT_EMBED_BATCH_PER_RUN (default 5000) caps how many events
this phase will embed in one incremental cycle, so a 6.28M-row backfill
does not wedge the scheduler. Use `python -m src.main embed-backfill` for
the offline drain.
"""
import logging
import os

logger = logging.getLogger(__name__)


async def embed_new_timeline_events(batch_size: int = 500, max_events: int | None = None) -> dict:
    """Embed timeline_events.title into timeline_embeddings.

    Args:
        batch_size: DB fetch + embed batch size per iteration. 500 keeps the
            embed step (~1-2s on CPU) small enough that a cancellation lands
            quickly, without wasting DB roundtrips.
        max_events: cap on total rows processed in this call. Defaults to
            TEXT_EMBED_BATCH_PER_RUN (5000) — the per-cycle budget for the
            scheduler. Backfill CLI passes None to run until drained.

    Returns:
        {"processed": N, "skipped": M, "batches": B, "model": name}
        On non-fatal failure (embedder load failed, etc.):
        {"skipped": "embedder_unavailable", "error": "..."}
    """
    from src.db.connection import get_analyzer_pool

    if max_events is None:
        max_events = int(os.getenv("TEXT_EMBED_BATCH_PER_RUN", "5000") or 5000)

    # Lazy-load the embedder here (not at module import) so a failed download
    # only affects THIS phase — the scheduler keeps ticking.
    try:
        from src.pipeline.text_embedder import get_embedder, text_sha1
        embedder = get_embedder()
    except Exception as e:  # noqa: BLE001 — non-fatal per spec
        logger.warning("content_embedding: embedder unavailable, skipping (%s)", e)
        return {"skipped": "embedder_unavailable", "error": str(e)[:500]}

    pool = get_analyzer_pool()
    processed = 0
    batches = 0
    remaining = max_events

    while remaining > 0:
        this_batch = min(batch_size, remaining)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT te.id::text AS id, te.entity_id::text AS entity_id,
                       te.source, te.occurred_at, te.title
                FROM timeline_events te
                WHERE te.title IS NOT NULL AND te.title <> ''
                  AND NOT EXISTS (
                    SELECT 1 FROM timeline_embeddings e WHERE e.event_id = te.id
                  )
                LIMIT $1
                """,
                this_batch,
            )
        if not rows:
            break

        titles = [r["title"] for r in rows]
        try:
            vectors = embedder.embed(titles, is_query=False)
        except Exception as e:  # noqa: BLE001
            logger.exception("content_embedding: embed() failed on batch %d", batches)
            return {"processed": processed, "batches": batches,
                    "skipped": "embed_failed", "error": str(e)[:500]}

        # pgvector wants the string literal '[f,f,f,...]' — asyncpg has no
        # native vector codec by default, and this format is what pgvector
        # accepts for both INSERT and index build. See pgvector docs.
        insert_rows = []
        for r, vec in zip(rows, vectors):
            emb_literal = "[" + ",".join(f"{float(x):.7f}" for x in vec) + "]"
            insert_rows.append((
                r["id"],
                r["occurred_at"],
                r["entity_id"],
                r["source"],
                emb_literal,
                embedder.MODEL_NAME,
                text_sha1(r["title"]),
            ))

        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO timeline_embeddings
                    (event_id, occurred_at, entity_id, source, embedding, model, text_sha1)
                VALUES ($1::uuid, $2, $3::uuid, $4, $5::vector, $6, $7)
                ON CONFLICT (event_id) DO UPDATE
                    SET embedding = EXCLUDED.embedding,
                        model = EXCLUDED.model,
                        text_sha1 = EXCLUDED.text_sha1,
                        created_at = NOW()
                    WHERE timeline_embeddings.text_sha1 <> EXCLUDED.text_sha1
                """,
                insert_rows,
            )

        processed += len(rows)
        batches += 1
        remaining -= len(rows)
        logger.info(
            "content_embedding: batch %d done (%d rows, %d/%d total this run)",
            batches, len(rows), processed, max_events,
        )

    stats = {
        "processed": processed,
        "batches": batches,
        "model": getattr(embedder, "MODEL_NAME", "unknown"),
        "max_events": max_events,
    }
    logger.info("content_embedding: %s", stats)
    return stats
