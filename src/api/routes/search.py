"""Axis-1 MVP: semantic timeline search endpoint.

GET /api/search/timeline?q=...&entity_id=&since=&until=&source=&limit=

Embeds the query via text_embedder (with the "query: " prefix e5 requires)
and runs a pgvector HNSW cosine-distance kNN against timeline_embeddings,
joined back to timeline_events for the display title. Returns cosine
similarity (1 - distance) plus event metadata.

Failure semantics: if the embedder fails to load (first-run download
issue on a restricted network), the endpoint returns HTTP 503 rather than
500 so the frontend can surface an informative "search is warming up"
banner without breaking the dashboard.

Performance note: the HNSW index is queried with default ef_search; for
the current row counts (< 100K rows initially, growing to ~6M as backfill
completes) this is well below the 50-200ms budget. Tune with
`SET LOCAL hnsw.ef_search` inside the pool if latency creeps up.
"""
from datetime import datetime
import logging
import time

from fastapi import APIRouter, HTTPException, Query

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/timeline")
async def search_timeline(
    q: str = Query(..., description="Free-text semantic search query"),
    entity_id: str | None = Query(None, description="Restrict to one entity's timeline"),
    since: datetime | None = Query(None, description="occurred_at >= ISO datetime"),
    until: datetime | None = Query(None, description="occurred_at <= ISO datetime"),
    source: str | None = Query(None, description="Platform filter (github, strava, ...)"),
    limit: int = Query(50, ge=1, le=200, description="Max results (1..200)"),
):
    """Semantic search over timeline_events via HNSW cosine kNN.

    Returns:
        {
          "results": [
            {"event_id", "entity_id", "platform", "occurred_at",
             "snippet", "score"},
            ...
          ],
          "took_ms": int,
          "model": "intfloat/multilingual-e5-small"
        }
    """
    q = (q or "").strip()
    if not q:
        # Per spec: empty q -> 400. FastAPI's Query(...) enforces presence
        # but an all-whitespace string still counts as provided.
        raise HTTPException(status_code=400, detail="Query 'q' must not be empty")

    t0 = time.monotonic()

    try:
        from src.pipeline.text_embedder import get_embedder
        embedder = get_embedder()
    except Exception as e:  # noqa: BLE001
        logger.exception("search_timeline: embedder unavailable")
        raise HTTPException(
            status_code=503,
            detail=f"Text embedder not ready: {str(e)[:200]}",
        )

    try:
        vec = embedder.embed([q], is_query=True)[0]
    except Exception as e:  # noqa: BLE001
        logger.exception("search_timeline: embed failed")
        raise HTTPException(status_code=500, detail=f"Embedding failed: {str(e)[:200]}")

    emb_literal = "[" + ",".join(f"{float(x):.7f}" for x in vec) + "]"

    conditions: list[str] = []
    params: list = [emb_literal]
    idx = 2
    if entity_id:
        conditions.append(f"te.entity_id = ${idx}::uuid")
        params.append(entity_id)
        idx += 1
    if since:
        conditions.append(f"te.occurred_at >= ${idx}")
        params.append(since)
        idx += 1
    if until:
        conditions.append(f"te.occurred_at <= ${idx}")
        params.append(until)
        idx += 1
    if source:
        conditions.append(f"te.source = ${idx}")
        params.append(source)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)

    sql = f"""
        SELECT te.id::text     AS event_id,
               te.entity_id::text AS entity_id,
               te.source          AS platform,
               te.occurred_at,
               te.title           AS snippet,
               1 - (emb.embedding <=> $1::vector) AS cosine_score
        FROM timeline_embeddings emb
        JOIN timeline_events te ON te.id = emb.event_id
        {where}
        ORDER BY emb.embedding <=> $1::vector
        LIMIT ${idx}
    """

    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    results = [
        {
            "event_id": r["event_id"],
            "entity_id": r["entity_id"],
            "platform": r["platform"],
            "occurred_at": r["occurred_at"].isoformat() if r["occurred_at"] else None,
            "snippet": r["snippet"],
            "score": round(float(r["cosine_score"]), 4),
        }
        for r in rows
    ]

    took_ms = int((time.monotonic() - t0) * 1000)
    return {
        "results": results,
        "took_ms": took_ms,
        "model": embedder.MODEL_NAME,
    }
