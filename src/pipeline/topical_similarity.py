"""Axis-1 MVP: cross-entity `topical_similarity` identity signal.

Fires when two entities' timeline embeddings cluster around the same
topic centroid (cosine >= TOPICAL_SIMILARITY_THRESHOLD). Weak evidence
by design — topical overlap is common among people in the same industry
or subculture, so this signal is registered with weight 0.15 in
identity_scorer._TYPE_WEIGHT (weaker than stylistic content_similarity
at 0.30) and NOT added to auto_labeler._HARD_SIGNALS.

Runs AFTER content_embedding (which populates timeline_embeddings) and
BEFORE identity_scoring. Reads only embeddings that already exist —
never triggers the embedder itself, so a missing model on the first run
just yields zero signals until the embed phase catches up.

Persistence: delete-then-executemany, one row per normalized (a<b)
entity pair, target_platform=NULL, target_record_id=entity_b_uuid_text
per spec.

Blocking-set filter: (a) skip self-pairs; (b) skip pairs already
dismissed by human labels (not auto-labels — those are training data,
not merges). The scorer additionally suppresses dismissed pairs at
scoring time, but suppressing them here saves the write.

Cost: O(E^2) in the number of entities WITH embeddings. For the 5k-10k
active entity range this is bounded (< 100M comparisons on 384-d
centroids, ~1-2s on numpy). If the count grows further, gate on entity
watch_status or precompute a coarse LSH index.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)


async def emit_topical_similarity_signals() -> dict:
    """Emit topical_similarity identity_signals rows.

    Returns stats {"entities_scanned", "pairs_scored", "signals_emitted",
    "below_threshold_skipped", "dismissed_skipped"}.
    """
    import numpy as np
    from src.db.connection import get_analyzer_pool

    # 0.95 default (raised from 0.85 on 2026-07-12): multilingual-e5-small
    # centroids of entity timelines cluster tightly (generic social-media text),
    # so 0.85 fired on ~62% of all pairs — flooding the scorer/review queue with
    # ~46k weak topical signals. 0.95 keeps only the most topically-aligned ~7%
    # (~5k), where this weak (0.15) corroborating signal is actually meaningful.
    threshold = float(os.getenv("TOPICAL_SIMILARITY_THRESHOLD", "0.95") or 0.95)
    max_events = int(os.getenv("TOPICAL_MAX_EVENTS_PER_ENTITY", "200") or 200)

    stats = {
        "entities_scanned": 0,
        "pairs_scored": 0,
        "signals_emitted": 0,
        "below_threshold_skipped": 0,
        "dismissed_skipped": 0,
    }

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        # Cap per entity via a windowed row_number over the most recent K
        # events. Postgres evaluates the CTE once, so this is a single scan
        # of timeline_embeddings.
        rows = await conn.fetch(
            """
            WITH ranked AS (
                SELECT te.entity_id::text AS entity_id,
                       te.embedding::text AS emb,
                       row_number() OVER (
                           PARTITION BY te.entity_id ORDER BY te.occurred_at DESC
                       ) AS rn
                FROM timeline_embeddings te
                -- Only entities that STILL exist. timeline_embeddings.entity_id is
                -- copied at embed time and can go stale after an entity merge/delete;
                -- emitting a topical_similarity signal for a vanished entity violates
                -- identity_signals_entity_id_fkey and aborts the whole phase.
                JOIN entities e ON e.id = te.entity_id
                WHERE te.entity_id IS NOT NULL
            )
            SELECT entity_id, emb FROM ranked WHERE rn <= $1
            """,
            max_events,
        )

        # Human dismissals — same filter used by identity_scorer. Auto-labels
        # are training data, NOT dismissals, so exclude anything with source
        # starting 'auto_'.
        dismissed_rows = await conn.fetch(
            """
            SELECT entity_a::text AS a, entity_b::text AS b
            FROM identity_labels
            WHERE label = 0 AND (source IS NULL OR source NOT LIKE 'auto\\_%' ESCAPE '\\')
            """
        )
        dismissed = {(r["a"], r["b"]) for r in dismissed_rows}

    if not rows:
        logger.info("topical_similarity: no timeline_embeddings yet, skipping")
        return stats

    # Group embeddings by entity. pgvector's text form is '[f,f,...]'; parse
    # it with json.loads (the format is JSON-array-compatible).
    by_entity: dict[str, list[list[float]]] = {}
    for r in rows:
        eid = r["entity_id"]
        try:
            vec = json.loads(r["emb"])
        except (TypeError, json.JSONDecodeError):
            continue
        by_entity.setdefault(eid, []).append(vec)

    stats["entities_scanned"] = len(by_entity)
    if len(by_entity) < 2:
        return stats

    entity_ids_sorted = sorted(by_entity.keys())
    n_events_map: dict[str, int] = {}
    centroids: list["np.ndarray"] = []
    for eid in entity_ids_sorted:
        M = np.asarray(by_entity[eid], dtype=np.float32)
        n_events_map[eid] = len(M)
        # Embeddings are already L2-normalized. Mean of unit vectors is not
        # itself unit-norm, so re-normalise the centroid before cosine math.
        centroid = M.mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm < 1e-9:
            # Degenerate — antiparallel unit vectors cancel out. Skip.
            centroid = np.zeros_like(centroid)
        else:
            centroid = centroid / norm
        centroids.append(centroid)

    C = np.stack(centroids, axis=0)  # (E, 384)
    # Cosine similarity matrix (E, E). Symmetric; upper triangle only.
    sims = C @ C.T
    np.fill_diagonal(sims, -1.0)  # so max isn't self

    new_signals: list[tuple] = []
    E = len(entity_ids_sorted)
    for i in range(E):
        a = entity_ids_sorted[i]
        for j in range(i + 1, E):
            b = entity_ids_sorted[j]
            stats["pairs_scored"] += 1
            score = float(sims[i, j])
            if score < threshold:
                stats["below_threshold_skipped"] += 1
                continue
            key = (a, b) if a < b else (b, a)
            if key in dismissed:
                stats["dismissed_skipped"] += 1
                continue

            value_json = json.dumps({
                "n_events_a": n_events_map[a],
                "n_events_b": n_events_map[b],
                "model": "intfloat/multilingual-e5-small",
            })
            # entity_a < entity_b by outer/inner loop order (already sorted).
            new_signals.append((
                key[0],
                "topical_similarity",
                "analyzer",         # source_platform
                "timeline_embeddings",  # source_table
                None,               # source_column
                None,               # source_record_id
                None,               # target_platform (per spec: NULL)
                key[1],             # target_record_id = entity_b uuid text
                value_json,
                round(score, 4),
            ))

    async with analyzer.acquire() as conn:
        await conn.execute(
            "DELETE FROM identity_signals WHERE signal_type = 'topical_similarity'"
        )
        if new_signals:
            await conn.executemany(
                """
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                new_signals,
            )

    stats["signals_emitted"] = len(new_signals)
    logger.info("topical_similarity: %s", stats)
    return stats
