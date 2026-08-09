"""Timeline search endpoint with keyword, semantic, and hybrid modes.

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
import os
from pathlib import Path
import time

from fastapi import APIRouter, HTTPException, Query

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


def _semantic_model_ready() -> bool:
    if os.getenv("TEXT_SEARCH_HYBRID_SEMANTIC", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    base = Path(os.getenv("TEXT_EMBED_MODEL_PATH") or Path(os.getenv("MEDIA_DERIVED_PATH", "/app/media_derived")) / "models" / "text_embedder")
    return (
        (base / "tokenizer.json").is_file()
        and (base / "config.json").is_file()
        and ((base / "onnx" / "model_quantized.onnx").is_file() or (base / "onnx" / "model.onnx").is_file())
    )


@router.get("/timeline")
async def search_timeline(
    q: str = Query(..., description="Free-text semantic search query"),
    mode: str = Query("hybrid", pattern="^(hybrid|keyword|semantic)$"),
    entity_id: str | None = Query(None, description="Restrict to one entity's timeline"),
    since: datetime | None = Query(None, description="occurred_at >= ISO datetime"),
    until: datetime | None = Query(None, description="occurred_at <= ISO datetime"),
    source: str | None = Query(None, description="Platform filter (github, strava, ...)"),
    limit: int = Query(50, ge=1, le=200, description="Max results (1..200)"),
):
    """Search timeline text features with Postgres FTS, pgvector, or RRF hybrid."""
    q = (q or "").strip()
    if not q:
        # Per spec: empty q -> 400. FastAPI's Query(...) enforces presence
        # but an all-whitespace string still counts as provided.
        raise HTTPException(status_code=400, detail="Query 'q' must not be empty")

    t0 = time.monotonic()

    emb_literal = None
    embedder = None
    if mode in {"semantic", "hybrid"}:
        if mode == "hybrid" and not _semantic_model_ready():
            logger.info("search_timeline: semantic model not local; hybrid falling back to keyword")
            mode = "keyword"
        else:
            try:
                from src.pipeline.text_embedder import get_embedder
                embedder = get_embedder()
                vec = embedder.embed([q], is_query=True)[0]
                emb_literal = "[" + ",".join(f"{float(x):.7f}" for x in vec) + "]"
            except Exception as e:  # noqa: BLE001
                if mode == "semantic":
                    logger.exception("search_timeline: embedder unavailable")
                    raise HTTPException(status_code=503, detail=f"Text embedder not ready: {str(e)[:200]}")
                logger.warning("search_timeline: hybrid falling back to keyword: %s", e)
                mode = "keyword"

    filter_values: list = []

    def _filters(start_idx: int) -> tuple[str, int]:
        conditions: list[str] = []
        idx = start_idx
        if entity_id:
            conditions.append(f"ttf.entity_id = ${idx}::uuid")
            idx += 1
        if since:
            conditions.append(f"ttf.occurred_at >= ${idx}")
            idx += 1
        if until:
            conditions.append(f"ttf.occurred_at <= ${idx}")
            idx += 1
        if source:
            conditions.append(f"ttf.source = ${idx}")
            idx += 1
        return (" AND " + " AND ".join(conditions)) if conditions else "", idx

    if entity_id:
        filter_values.append(entity_id)
    if since:
        filter_values.append(since)
    if until:
        filter_values.append(until)
    if source:
        filter_values.append(source)

    if mode == "keyword":
        filter_sql, limit_idx = _filters(2)
        params = [q, *filter_values, limit]
        sql = f"""
            WITH query AS (SELECT websearch_to_tsquery('simple', $1) AS tsq)
            SELECT ranked.event_id::text AS event_id,
                   ranked.entity_id::text AS entity_id,
                   ranked.source AS platform,
                   ranked.occurred_at,
                   LEFT(ranked.canonical_text, 500) AS snippet,
                   ranked.keyword_score AS score,
                   ranked.keyword_score,
                   NULL::float8 AS semantic_score,
                   ranked.keyword_rank,
                   NULL::int AS semantic_rank,
                   ranked.keyword_rank AS rrf_rank
            FROM (
                SELECT ttf.*,
                       ts_rank_cd(ttf.search_vector, query.tsq) AS keyword_score,
                       row_number() OVER (ORDER BY ts_rank_cd(ttf.search_vector, query.tsq) DESC, ttf.occurred_at DESC)::int AS keyword_rank
                FROM timeline_text_features ttf, query
                WHERE ttf.search_vector @@ query.tsq {filter_sql}
            ) ranked
            ORDER BY ranked.keyword_rank
            LIMIT ${limit_idx}
        """
    elif mode == "semantic":
        filter_sql, limit_idx = _filters(2)
        params = [emb_literal, *filter_values, limit]
        sql = f"""
            SELECT ttf.event_id::text AS event_id,
                   ttf.entity_id::text AS entity_id,
                   ttf.source AS platform,
                   ttf.occurred_at,
                   LEFT(ttf.canonical_text, 500) AS snippet,
                   1 - (emb.embedding <=> $1::vector) AS score,
                   NULL::float8 AS keyword_score,
                   1 - (emb.embedding <=> $1::vector) AS semantic_score,
                   NULL::int AS keyword_rank,
                   row_number() OVER (ORDER BY emb.embedding <=> $1::vector)::int AS semantic_rank,
                   row_number() OVER (ORDER BY emb.embedding <=> $1::vector)::int AS rrf_rank
            FROM timeline_embeddings emb
            JOIN timeline_text_features ttf ON ttf.event_id = emb.event_id
            WHERE TRUE {filter_sql}
            ORDER BY emb.embedding <=> $1::vector
            LIMIT ${limit_idx}
        """
    else:
        filter_sql, limit_idx = _filters(3)
        params = [q, emb_literal, *filter_values, limit]
        sql = f"""
            WITH query AS (SELECT websearch_to_tsquery('simple', $1) AS tsq),
            keyword AS (
                SELECT ttf.event_id,
                       ts_rank_cd(ttf.search_vector, query.tsq) AS keyword_score,
                       row_number() OVER (ORDER BY ts_rank_cd(ttf.search_vector, query.tsq) DESC, ttf.occurred_at DESC)::int AS keyword_rank
                FROM timeline_text_features ttf, query
                WHERE ttf.search_vector @@ query.tsq {filter_sql}
                ORDER BY keyword_rank
                LIMIT ${limit_idx}
            ),
            semantic AS (
                SELECT ttf.event_id,
                       1 - (emb.embedding <=> $2::vector) AS semantic_score,
                       row_number() OVER (ORDER BY emb.embedding <=> $2::vector)::int AS semantic_rank
                FROM timeline_embeddings emb
                JOIN timeline_text_features ttf ON ttf.event_id = emb.event_id
                WHERE TRUE {filter_sql}
                ORDER BY semantic_rank
                LIMIT ${limit_idx}
            ),
            fused AS (
                SELECT COALESCE(k.event_id, s.event_id) AS event_id,
                       k.keyword_score,
                       s.semantic_score,
                       k.keyword_rank,
                       s.semantic_rank,
                       COALESCE(1.0 / (60 + k.keyword_rank), 0) + COALESCE(1.0 / (60 + s.semantic_rank), 0) AS rrf_score
                FROM keyword k
                FULL OUTER JOIN semantic s ON s.event_id = k.event_id
            )
            SELECT ttf.event_id::text AS event_id,
                   ttf.entity_id::text AS entity_id,
                   ttf.source AS platform,
                   ttf.occurred_at,
                   LEFT(ttf.canonical_text, 500) AS snippet,
                   fused.rrf_score AS score,
                   fused.keyword_score,
                   fused.semantic_score,
                   fused.keyword_rank,
                   fused.semantic_rank,
                   row_number() OVER (ORDER BY fused.rrf_score DESC, ttf.occurred_at DESC)::int AS rrf_rank
            FROM fused
            JOIN timeline_text_features ttf ON ttf.event_id = fused.event_id
            ORDER BY fused.rrf_score DESC, ttf.occurred_at DESC
            LIMIT ${limit_idx}
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
            "score": round(float(r["score"] or 0), 4),
            "keyword_score": round(float(r["keyword_score"]), 4) if r["keyword_score"] is not None else None,
            "semantic_score": round(float(r["semantic_score"]), 4) if r["semantic_score"] is not None else None,
            "keyword_rank": r["keyword_rank"],
            "semantic_rank": r["semantic_rank"],
            "rrf_rank": r["rrf_rank"],
        }
        for r in rows
    ]

    took_ms = int((time.monotonic() - t0) * 1000)
    return {
        "results": results,
        "took_ms": took_ms,
        "mode": mode,
        "model": getattr(embedder, "MODEL_NAME", None),
    }
