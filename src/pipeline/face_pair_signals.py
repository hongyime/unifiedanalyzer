"""Axis-3 Change-3: cross-entity `face_pair_knn` identity signal.

Fires when two DISTINCT entities have faces close in ArcFace embedding space,
independent of cluster co-membership. Complements the cluster-based
`media_face_match` builder in media_analysis_tier1:

  - media_face_match: threshold 0.40, no portrait gate, O(n^2) BLAS over every
    bridged face (noisy but broad).
  - face_pair_knn (this module): threshold 0.55, PORTRAIT-only (source image
    face_count <= 3), REQUIRES >= 2 face-pair matches — so a single lookalike
    or sibling face can't fabricate the signal. Registered as a hard-anchor in
    auto_labeler and weighted 0.60 in identity_scorer._TYPE_WEIGHT (see the
    Axis-3 append-only registrations there).

Storage: one identity_signals row per normalized entity pair (entity_a
< entity_b), signal_type='face_pair_knn', target_platform='facetracker',
target_record_id=entity_b_uuid_text, source_table='facetracker.faces',
confidence=max_cosine, value={n_matches, max_cosine}. Follows the delete-then-
executemany persistence pattern used by contact_extraction /
route_similarity so a rebuild is atomic.

Deps: numpy always; faiss is optional (lazy-imported for the pairwise math),
we fall back to a numpy dot on missing faiss so the phase never hard-fails on
a missing optional dep — matching the graceful-skip pattern in
face_clustering.propagate_drive_faces_via_knn.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

# Portrait gate: only trust faces from images with at most this many detected
# faces (selfies/portraits, not crowds). face_count is written by the face_worker
# at ingest time and is fail-CLOSED via _UNKNOWN_FACE_COUNT elsewhere; here we
# just enforce the low-face-count admission.
_PORTRAIT_MAX_FACES = 3

# Axis-3 Change-3 env defaults:
_PAIR_KNN_THRESHOLD = float(os.getenv("FACE_PAIR_KNN_THRESHOLD", "0.55"))
_PAIR_KNN_MIN_MATCHES = int(os.getenv("FACE_PAIR_KNN_MIN_MATCHES", "2"))
_PAIR_KNN_MAX_FACES_PER_ENTITY = int(os.getenv("FACE_PAIR_KNN_MAX_FACES_PER_ENTITY", "20"))


def _pair_key(a: str, b: str) -> tuple[str, str]:
    """Normalized (entity_a < entity_b) pair ordering — same convention as
    identity_scorer._pair_key and contact_extraction's shared_email signal
    emission, so a single delete-then-insert pass owns one row per pair."""
    return (a, b) if a < b else (b, a)


async def emit_face_pair_signals() -> dict:
    """Emit face_pair_knn identity_signals rows for every cross-entity pair
    whose bridged PORTRAIT faces cross the (threshold, min-matches) gates.

    Returns per-run stats. Never raises on a missing optional dep — a faiss
    absence just falls through to numpy.dot on the (already L2-normalized)
    embedding matrices, and numpy is a hard dep of every phase.
    """
    import numpy as np
    from src.db.connection import get_analyzer_pool

    stats = {"entities_scanned": 0, "pairs_scored": 0,
             "signals_emitted": 0, "below_threshold_skipped": 0,
             "below_min_matches_skipped": 0}

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            WITH contested_clusters AS (
                SELECT f.cluster_id
                FROM facetracker.faces f
                JOIN public.entity_faces ef ON ef.face_id = f.id
                WHERE f.cluster_id IS NOT NULL
                  AND NOT COALESCE(f.is_junk, FALSE)
                GROUP BY f.cluster_id
                HAVING count(DISTINCT ef.entity_id) > 1
            )
            SELECT ef.entity_id::text AS entity_id, f.id AS face_id,
                   f.embedding_vec::text AS emb,
                   COALESCE(f.quality_score, 0.0) AS q
            FROM public.entity_faces ef
            JOIN facetracker.faces f ON f.id = ef.face_id
            JOIN facetracker.images i ON i.id = f.image_id
            WHERE f.embedding_vec IS NOT NULL
              AND NOT COALESCE(f.is_junk, FALSE)
              AND i.face_count IS NOT NULL
              AND i.face_count <= $1
              AND (
                    f.cluster_id IS NULL
                    OR NOT EXISTS (
                        SELECT 1
                        FROM contested_clusters cc
                        WHERE cc.cluster_id = f.cluster_id
                    )
                  )
        """, _PORTRAIT_MAX_FACES)

    if not rows:
        return stats

    # Group faces by entity, cap to _PAIR_KNN_MAX_FACES_PER_ENTITY by quality
    # so a super-active entity doesn't dominate the O(n_a * n_b) inner loop.
    by_entity: dict[str, list[tuple[int, list[float], float]]] = {}
    for r in rows:
        eid = r["entity_id"]
        emb = json.loads(r["emb"])
        by_entity.setdefault(eid, []).append((int(r["face_id"]), emb, float(r["q"])))
    for eid, faces in by_entity.items():
        if len(faces) > _PAIR_KNN_MAX_FACES_PER_ENTITY:
            faces.sort(key=lambda t: t[2], reverse=True)  # quality desc
            by_entity[eid] = faces[:_PAIR_KNN_MAX_FACES_PER_ENTITY]
    stats["entities_scanned"] = len(by_entity)

    if len(by_entity) < 2:
        return stats

    # L2-normalize each entity's face matrix once; dot product == cosine.
    entity_matrices: dict[str, "np.ndarray"] = {}
    for eid, faces in by_entity.items():
        M = np.asarray([f[1] for f in faces], dtype=np.float32)
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        entity_matrices[eid] = M / norms

    entity_ids_sorted = sorted(by_entity.keys())
    new_signals: list[tuple] = []
    for i, a in enumerate(entity_ids_sorted):
        for b in entity_ids_sorted[i + 1:]:
            stats["pairs_scored"] += 1
            Ma, Mb = entity_matrices[a], entity_matrices[b]
            sims = Ma @ Mb.T  # (n_a, n_b) cosine similarity matrix
            max_sim = float(sims.max())
            if max_sim < _PAIR_KNN_THRESHOLD:
                stats["below_threshold_skipped"] += 1
                continue
            n_matches = int(np.sum(sims >= _PAIR_KNN_THRESHOLD))
            if n_matches < _PAIR_KNN_MIN_MATCHES:
                stats["below_min_matches_skipped"] += 1
                continue
            # entity_a < entity_b guaranteed by outer loop order.
            new_signals.append((
                a, "face_pair_knn", "facetracker", "facetracker.faces", None, None,
                "facetracker", b,
                json.dumps({"n_matches": n_matches, "max_cosine": round(max_sim, 4)}),
                max_sim,
            ))

    async with analyzer.acquire() as conn:
        await conn.execute(
            "DELETE FROM identity_signals WHERE signal_type = 'face_pair_knn'"
        )
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)

    stats["signals_emitted"] = len(new_signals)
    logger.info("face_pair_knn signals: %s", stats)
    return stats
