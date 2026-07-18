"""Face clustering + entity_faces propagation (signals-only).

WHY: the `media_face_match` identity signal pipeline already exists
(`media_analysis_tier1._build_face_match_signals`, scorer weight 0.50) but is
**starved** — it only matches faces already bridged to entities via
`public.entity_faces`, and only ~13 of ~2.3k detected faces are bridged. This
module groups `facetracker.faces` by their InsightFace/ArcFace 512-d embedding
into person clusters, then **propagates** the existing entity attribution across
each cluster so that bridged corpus grows. The existing signal builder then
fires with real data.

DESIGN (per user decision 2026-06-26): **auto-propagate / signals-only**. We
grow `entity_faces` and let the scorer's `media_face_match` signal fire; we never
auto-merge entities here. Human merge flow is untouched.

GROUP-PHOTO GUARD: `face_worker.ingest_collector_media` bridges *every* face in
a poster's media to that poster — fine for a selfie, noisy for a posted group
photo (bystanders get bridged to the poster). To avoid amplifying that noise we
only propagate a cluster's dominant entity to sibling faces drawn from
LOW-face-count images (likely selfies/portraits), and only when the cluster has
a single unambiguous dominant entity.

DRIVE FACES (task #9): owner-less faces from the W/X/Y/Z drive scan share the
same embedding space, so they land in the same clusters. When a drive face joins
a cluster with a dominant entity it is bridged too (method='drive_cross_ref') —
turning the drive scan into a source of an entity's offline photos.

SCALE NOTE: agglomerative clustering is O(n^2) memory. Bounded by
FACE_CLUSTER_MAX (top-N by quality). For >~50k faces, swap to a FAISS-kNN graph
+ community detection (TODO).
"""
import asyncio
import json
import logging
import os
from uuid import UUID

import numpy as np
from asyncpg.exceptions import QueryCanceledError

from src.db.connection import get_analyzer_pool, get_collector_pool, is_collector_unavailable_error

logger = logging.getLogger(__name__)

# Cosine similarity at/above which two ArcFace embeddings are the same person.
# ArcFace same-person pairs typically land 0.45-0.7, impostors below ~0.2; the
# existing media_face_match builder uses 0.40. We cluster a touch stricter
# (precision-first — over-splitting is safer than merging two people).
_CLUSTER_THRESHOLD = float(os.getenv("FACE_CLUSTER_THRESHOLD", "0.50"))
# Max faces to cluster per pass (top-N by quality). With the FAISS-kNN backend
# (P2-6) the memory cost is O(n*k) not O(n^2), so this can be raised well past
# the old agglomerative ceiling.
_FACE_CLUSTER_MAX = int(os.getenv("FACE_CLUSTER_MAX", "20000"))
# P2-6: neighbours per face in the kNN graph. Cluster = connected component over
# MUTUAL kNN edges at/above _CLUSTER_THRESHOLD. Mutual-kNN (both faces list each
# other) resists the single-linkage chaining that a plain threshold graph — or
# single-linkage agglomerative — suffers, which is why the old code used
# average-linkage. Combined with the P1-2 propagation purity gate, two distinct
# people are far less likely to merge through one borderline bridge face.
_FACE_KNN = int(os.getenv("FACE_CLUSTER_KNN", "20"))
# Exact flat kNN is quadratic in runtime. Keep it for small corpora where it is
# deterministic and cheap; use bounded HNSW for larger full-resolution runs.
_FACE_EXACT_SEARCH_MAX = int(os.getenv("FACE_CLUSTER_EXACT_SEARCH_MAX", "5000"))
_FACE_HNSW_M = int(os.getenv("FACE_CLUSTER_HNSW_M", "32"))
_FACE_HNSW_EF_CONSTRUCTION = int(os.getenv("FACE_CLUSTER_HNSW_EF_CONSTRUCTION", "80"))
_FACE_HNSW_EF_SEARCH = int(os.getenv("FACE_CLUSTER_HNSW_EF_SEARCH", "64"))
_FACE_FAISS_THREADS = int(os.getenv("FACE_CLUSTER_FAISS_THREADS", "2"))
# Group-photo guard: only propagate attribution to sibling faces from images
# with at most this many detected faces (selfies/portraits, not crowds).
_MAX_PROPAGATE_FACE_COUNT = int(os.getenv("FACE_CLUSTER_MAX_GROUP_FACES", "3"))
# P1-2 (identity_system_review_plan.md): purity guards on propagation.
# _PROPAGATE_MIN_SIM: a candidate face is only bridged to the cluster's dominant
# entity if its cosine similarity to an already-bridged (anchor) face of that
# entity is at least this — STRICTER than the clustering threshold, so a face that
# only chained into the cluster through a borderline bridge is not absorbed.
_PROPAGATE_MIN_SIM = float(os.getenv("FACE_PROPAGATE_MIN_SIM", "0.55"))
# _PROPAGATE_MIN_QUALITY: skip low-quality crops (blurry/tiny/occluded) whose
# embeddings are unreliable. 0.0 disables (scale depends on the detector's
# quality_score); raise once the score distribution is known.
_PROPAGATE_MIN_QUALITY = float(os.getenv("FACE_PROPAGATE_MIN_QUALITY", "0.0"))
# Fail-CLOSED default when an image's face_count is unknown (NULL): treat it as a
# possible crowd so a missing count never silently propagates a bystander to the
# poster. (Was COALESCE(...,1) — fail-open — which the review flagged.)
_UNKNOWN_FACE_COUNT = 999

# Axis-3 Change-1 (purity guard hardening):
#
# _PURITY_2ND_NEAREST_THRESHOLD: reject cluster-based propagation when ANY
# bridged face of a COMPETING entity is at least this cosine-similar to any
# cluster member (anchor). Defends against propagating an anchor entity's label
# through a cluster that's actually a mix of two lookalikes (siblings, close
# relatives). Same threshold as _PROPAGATE_MIN_SIM by default — if a competitor
# clears the propagation floor, we don't trust the cluster.
_PURITY_2ND_NEAREST_THRESHOLD = float(os.getenv("FACE_PURITY_2ND_NEAREST_THRESHOLD", "0.55"))
# _PURITY_MIN_TIGHTNESS: reject if the tightest anchor-to-anchor pairwise cosine
# in the cluster falls below this. Loose clusters (single-linkage chains through
# borderline bridges) propagate bystanders — measured as the MINIMUM cosine
# between the anchor faces themselves, so a cluster that only barely coheres is
# not trusted enough to bridge new faces.
_PURITY_MIN_TIGHTNESS = float(os.getenv("FACE_PURITY_MIN_TIGHTNESS", "0.35"))

# Axis-3 Change-2 (drive-face kNN cross-ref):
_FACE_KNN_PROPAGATION_THRESHOLD = float(os.getenv("FACE_KNN_PROPAGATION_THRESHOLD", "0.62"))
_FACE_KNN_PROPAGATION_BATCH = int(os.getenv("FACE_KNN_PROPAGATION_BATCH", "20000"))
_FACE_MEDIA_ATTRIBUTION_MAX_FACES = int(os.getenv("FACE_MEDIA_ATTRIBUTION_MAX_FACES", "1"))
_DRIVE_XREF_THRESHOLD = float(os.getenv("FACE_DRIVE_XREF_THRESHOLD", str(_FACE_KNN_PROPAGATION_THRESHOLD)))
_DRIVE_XREF_TOP_MARGIN = float(os.getenv("FACE_DRIVE_XREF_TOP_MARGIN", "0.05"))
_DRIVE_XREF_BATCH = int(os.getenv("FACE_DRIVE_XREF_BATCH", "5000"))
_DRIVE_XREF_EXACT_PAIR_MAX = int(os.getenv("FACE_DRIVE_XREF_EXACT_PAIR_MAX", "250000"))

# Internal gate: skip the whole pass when the face corpus hasn't grown.
_last_face_count = -1


async def _ensure_schema(conn) -> None:
    """Idempotent DDL: a cluster_id on faces + a cluster metadata table."""
    await conn.execute("ALTER TABLE facetracker.faces ADD COLUMN IF NOT EXISTS cluster_id INTEGER")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_cluster ON facetracker.faces(cluster_id)")
    # Q3 junk gate: is_junk marks faces that the quality gate deems NOT a real
    # face (or too low-quality to trust). Junk faces are excluded from clustering,
    # entity bridging, and identity building. NULL/false = kept.
    await conn.execute("ALTER TABLE facetracker.faces ADD COLUMN IF NOT EXISTS is_junk BOOLEAN NOT NULL DEFAULT FALSE")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_is_junk ON facetracker.faces(is_junk) WHERE is_junk")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS facetracker.face_clusters (
            cluster_id          INTEGER PRIMARY KEY,
            size                INTEGER NOT NULL,
            dominant_entity_id  UUID,
            created_at          TIMESTAMPTZ DEFAULT NOW()
        )
    """)


# ── Q3: face-quality gate (mark obvious non-faces / untrustworthy crops) ──

# Env-tunable thresholds for flagging EXISTING faces as junk. Defaults are
# conservative (spot-checking showed even 0.62-0.72 det_score crops are usually
# real faces, incl. stylised/drawn ones) so a real face is not dropped. Raise
# FACE_JUNK_MIN_CONFIDENCE toward 0.60-0.65 to be more aggressive.
_JUNK_MIN_CONFIDENCE = float(os.getenv("FACE_JUNK_MIN_CONFIDENCE", "0.55"))
_JUNK_MIN_AREA_PERCENT = float(os.getenv("FACE_JUNK_MIN_AREA_PERCENT", "0"))
_JUNK_MIN_LAPLACIAN = float(os.getenv("FACE_JUNK_MIN_LAPLACIAN", "0"))
_JUNK_MAX_ASPECT = float(os.getenv("FACE_JUNK_MAX_ASPECT", "2.6"))


async def flag_junk_faces() -> dict:
    """Q3: mark obvious non-faces / untrustworthy crops in facetracker.faces as
    is_junk=TRUE so they are excluded from clustering + entity bridging.

    Tunable via env (see module constants). A face is junk if ANY of:
      * detection_confidence < FACE_JUNK_MIN_CONFIDENCE  (RetinaFace det_score —
        the strongest non-face discriminator)
      * face_area_percent    < FACE_JUNK_MIN_AREA_PERCENT (0 = disabled)
      * laplacian_variance   < FACE_JUNK_MIN_LAPLACIAN     (0 = disabled)
      * bbox aspect ratio    > FACE_JUNK_MAX_ASPECT        (banner/logo strip)

    Idempotent: recomputes the flag every pass (also UN-flags faces that would
    now pass, e.g. after the operator relaxes a threshold). Returns before/after
    counts. Bridged entity_faces rows for newly-junked faces are removed so junk
    can't leak into the social-face graph."""
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        await _ensure_schema(conn)
        before = await conn.fetchval("SELECT count(*) FROM facetracker.faces WHERE is_junk")
        total = await conn.fetchval("SELECT count(*) FROM facetracker.faces")
        # Aspect uses pixel bbox (nullable-safe). GREATEST(w/h, h/w).
        await conn.execute(
            """
            UPDATE facetracker.faces f SET is_junk = (
                COALESCE(f.detection_confidence, 1.0) < $1
                OR ($2 > 0 AND COALESCE(f.face_area_percent, 100.0) < $2)
                OR ($3 > 0 AND COALESCE(f.laplacian_variance, 1e9) < $3)
                OR (
                    (f.bbox_px_x2 - f.bbox_px_x1) > 0 AND (f.bbox_px_y2 - f.bbox_px_y1) > 0
                    AND GREATEST(
                        (f.bbox_px_x2 - f.bbox_px_x1)::float / NULLIF(f.bbox_px_y2 - f.bbox_px_y1, 0),
                        (f.bbox_px_y2 - f.bbox_px_y1)::float / NULLIF(f.bbox_px_x2 - f.bbox_px_x1, 0)
                    ) > $4
                )
            )
            """,
            _JUNK_MIN_CONFIDENCE, _JUNK_MIN_AREA_PERCENT, _JUNK_MIN_LAPLACIAN, _JUNK_MAX_ASPECT,
        )
        after = await conn.fetchval("SELECT count(*) FROM facetracker.faces WHERE is_junk")
        # Purge entity_faces + cluster_id for junk faces so downstream joins
        # never see them.
        purged = await conn.fetchval(
            "WITH d AS (DELETE FROM public.entity_faces ef "
            "USING facetracker.faces f WHERE ef.face_id = f.id AND f.is_junk RETURNING ef.face_id) "
            "SELECT count(*) FROM d"
        )
        await conn.execute("UPDATE facetracker.faces SET cluster_id = NULL WHERE is_junk AND cluster_id IS NOT NULL")
    stats = {"total": total, "junk_before": before, "junk_after": after,
             "kept": total - after, "entity_faces_purged": purged}
    logger.info("flag_junk_faces: %s", stats)
    return stats


def _knn_connected_components(M: "np.ndarray", threshold: float, k: int) -> "np.ndarray":
    """P2-6: cluster normalized embeddings via a FAISS mutual-kNN graph +
    connected components. O(n*k) memory instead of the agglomerative O(n^2)
    distance matrix, so it scales to 100k+ faces. Returns an int label per row.

    Edges are MUTUAL (i in j's top-k AND j in i's top-k) and >= threshold cosine —
    mutual-kNN resists single-linkage chaining. Falls back to sklearn if faiss is
    unavailable so clustering never hard-fails on a missing optional dep."""
    n = len(M)
    k = min(k + 1, n)  # +1 because the first neighbour is the face itself
    try:
        import faiss
        if _FACE_FAISS_THREADS > 0 and hasattr(faiss, "omp_set_num_threads"):
            faiss.omp_set_num_threads(_FACE_FAISS_THREADS)

        if n <= _FACE_EXACT_SEARCH_MAX:
            logger.info("cluster_faces: using exact FAISS search for %d faces", n)
            index = faiss.IndexFlatIP(M.shape[1])  # inner product on normalized = cosine
        else:
            logger.info(
                "cluster_faces: using FAISS HNSW search for %d faces "
                "(exact_limit=%d, m=%d, ef_search=%d)",
                n, _FACE_EXACT_SEARCH_MAX, _FACE_HNSW_M, _FACE_HNSW_EF_SEARCH,
            )
            index = faiss.IndexHNSWFlat(M.shape[1], _FACE_HNSW_M, faiss.METRIC_INNER_PRODUCT)
            index.hnsw.efConstruction = _FACE_HNSW_EF_CONSTRUCTION
            index.hnsw.efSearch = _FACE_HNSW_EF_SEARCH
        index.add(M)
        sims, idxs = index.search(M, k)
    except Exception:
        if n > _FACE_EXACT_SEARCH_MAX:
            logger.warning(
                "FAISS large-corpus search failed; failing closed to singleton face clusters",
                exc_info=True,
            )
            return np.arange(n, dtype=np.int64)
        logger.warning("faiss unavailable; falling back to agglomerative clustering", exc_info=True)
        from sklearn.cluster import AgglomerativeClustering
        return AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average",
            distance_threshold=1.0 - threshold,
        ).fit_predict(M)

    # Directed neighbour sets at/above threshold (excluding self), then keep only
    # mutual edges.
    neighbours: list[set[int]] = [set() for _ in range(n)]
    for i in range(n):
        for sim, j in zip(sims[i], idxs[i]):
            if j != i and j >= 0 and sim >= threshold:
                neighbours[i].add(int(j))

    # Union-find over mutual edges.
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in neighbours[i]:
            if i in neighbours[j]:  # mutual
                union(i, j)

    # Compact roots into contiguous label ids.
    labels = np.empty(n, dtype=np.int64)
    root_to_label: dict[int, int] = {}
    for i in range(n):
        r = find(i)
        lbl = root_to_label.get(r)
        if lbl is None:
            lbl = len(root_to_label)
            root_to_label[r] = lbl
        labels[i] = lbl
    return labels


async def cluster_faces() -> dict:
    """Cluster facetracker.faces by embedding; write cluster_id back. Returns
    stats. P2-6: uses a FAISS mutual-kNN graph + connected components (O(n*k)
    memory), replacing the old average-linkage agglomerative O(n^2) matrix so it
    scales past ~30-50k faces.

    Instrumentation (2026-07-08): every heavy sync step logs its start + wall
    time, and the CPU-bound blocks (JSON parse + numpy normalize + FAISS kNN)
    run via asyncio.to_thread so the scheduler's heartbeat loop keeps firing.
    This prevents the pattern where a deadlock in one FAISS thread wedged the
    whole event loop and kept run_phase_status blank for hours."""
    import time
    t0 = time.monotonic()
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        await _ensure_schema(conn)
        logger.info("cluster_faces: loading embeddings from facetracker.faces (LIMIT %d)", _FACE_CLUSTER_MAX)
        rows = await conn.fetch("""
            SELECT id, embedding_vec::text AS emb
            FROM facetracker.faces
            WHERE embedding_vec IS NOT NULL
              AND NOT is_junk
            ORDER BY quality_score DESC NULLS LAST
            LIMIT $1
        """, _FACE_CLUSTER_MAX)
    logger.info("cluster_faces: loaded %d rows in %.1fs", len(rows), time.monotonic() - t0)

    if len(rows) < 2:
        return {"faces": len(rows), "clusters": 0, "skipped": "too_few_faces"}

    def _parse_and_normalize(rows_):
        face_ids_ = [r["id"] for r in rows_]
        M_ = np.asarray([json.loads(r["emb"]) for r in rows_], dtype=np.float32)
        norms = np.linalg.norm(M_, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        M_ /= norms
        return face_ids_, M_

    t1 = time.monotonic()
    face_ids, M = await asyncio.to_thread(_parse_and_normalize, rows)
    logger.info("cluster_faces: parsed+normalized %d embeddings in %.1fs (shape=%s)",
                len(face_ids), time.monotonic() - t1, M.shape)

    t2 = time.monotonic()
    labels = await asyncio.to_thread(
        _knn_connected_components, M, _CLUSTER_THRESHOLD, _FACE_KNN
    )
    logger.info("cluster_faces: kNN+connected-components in %.1fs (unique_labels=%d)",
                time.monotonic() - t2, len(set(int(x) for x in labels)))

    # Persist assignments + cluster sizes.
    sizes: dict[int, int] = {}
    assignments = []
    for fid, lab in zip(face_ids, labels):
        lab = int(lab)
        sizes[lab] = sizes.get(lab, 0) + 1
        assignments.append((lab, fid))

    t3 = time.monotonic()
    async with analyzer.acquire() as conn:
        async with conn.transaction():
            # Reset only the faces we re-clustered this pass.
            await conn.execute(
                "UPDATE facetracker.faces SET cluster_id = NULL WHERE id = ANY($1::int[])",
                face_ids,
            )
            await conn.executemany(
                "UPDATE facetracker.faces SET cluster_id = $1 WHERE id = $2", assignments
            )
            await conn.execute("DELETE FROM facetracker.face_clusters")
            await conn.executemany(
                "INSERT INTO facetracker.face_clusters (cluster_id, size) VALUES ($1, $2)",
                list(sizes.items()),
            )
    logger.info("cluster_faces: persisted %d assignments in %.1fs", len(assignments), time.monotonic() - t3)

    multi = sum(1 for s in sizes.values() if s > 1)
    logger.info("cluster_faces: DONE total_wall=%.1fs faces=%d clusters=%d multi_face=%d",
                time.monotonic() - t0, len(face_ids), len(sizes), multi)
    return {"faces": len(face_ids), "clusters": len(sizes), "multi_face_clusters": multi}


def _normalized_matrix(emb_texts: list[str]) -> "np.ndarray | None":
    """Parse pgvector text embeddings into an L2-normalized (n, d) matrix, so a
    dot product is cosine similarity. Returns None if there are no embeddings."""
    if not emb_texts:
        return None
    M = np.asarray([json.loads(t) for t in emb_texts], dtype=np.float32)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return M / norms


def _passes_purity(cand_emb_text: str, anchors: "np.ndarray | None") -> bool:
    """True if the candidate face is at least _PROPAGATE_MIN_SIM cosine-similar to
    any anchor (already-bridged) face of the dominant entity. Without anchors we
    cannot verify purity, so we fail closed (do not propagate)."""
    if anchors is None or len(anchors) == 0:
        return False
    v = np.asarray(json.loads(cand_emb_text), dtype=np.float32)
    n = np.linalg.norm(v)
    if n == 0:
        return False
    v /= n
    return float(np.max(anchors @ v)) >= _PROPAGATE_MIN_SIM


def _competing_entity_too_close(
    anchors: "np.ndarray | None",
    competitor_embs: list[str],
) -> bool:
    """Axis-3 Change-1: second-nearest-entity guard. Returns True iff ANY face of
    a competing (non-anchor) entity is at least _PURITY_2ND_NEAREST_THRESHOLD
    cosine-similar to any of the dominant entity's anchor faces. If it is, the
    cluster is ambiguous in embedding space and we must NOT propagate the anchor
    entity's label — the same neighborhood already contains a different person's
    bridged face.

    Fail-open on missing input: no anchors or no competitors => not too close.
    (An empty-anchors case is already caught upstream by _passes_purity's fail-
    closed policy, so returning False here is harmless.)"""
    if anchors is None or len(anchors) == 0 or not competitor_embs:
        return False
    M = _normalized_matrix(competitor_embs)
    if M is None or len(M) == 0:
        return False
    # anchors: (a, d), M: (c, d). Max pairwise cosine == max(anchors @ M.T).
    sims = anchors @ M.T
    return float(np.max(sims)) >= _PURITY_2ND_NEAREST_THRESHOLD


def _cluster_too_loose(anchors: "np.ndarray | None") -> bool:
    """Axis-3 Change-1: intra-cluster tightness guard. Returns True iff the
    tightest anchor-to-anchor pairwise cosine falls below _PURITY_MIN_TIGHTNESS
    — i.e. the anchor faces themselves already disagree, so the cluster is a
    loose chain rather than one person. Single-anchor clusters have no pairwise
    distance to measure, so return False (fail open — the existing
    _passes_purity gate still governs candidate admission)."""
    if anchors is None or len(anchors) < 2:
        return False
    # Self-similarity on the diagonal would always be 1.0; mask it before min.
    sims = anchors @ anchors.T
    n = sims.shape[0]
    mask = ~np.eye(n, dtype=bool)
    if not mask.any():
        return False
    return float(np.min(sims[mask])) < _PURITY_MIN_TIGHTNESS


async def propagate_entity_faces() -> dict:
    """For each cluster with a single unambiguous dominant entity, bridge the
    cluster's currently-unbridged faces to that entity (group-photo guarded).
    Grows public.entity_faces so the existing media_face_match builder fires.

    Returns stats incl. how many drive-origin faces got cross-referenced.
    """
    import time
    _t = time.monotonic()
    logger.info("propagate_entity_faces: starting")
    analyzer = get_analyzer_pool()
    stats = {"clusters_with_entity": 0, "propagated": 0, "drive_cross_ref": 0,
             "ambiguous_skipped": 0, "impurity_skipped": 0,
             "competitor_skipped": 0, "loose_cluster_skipped": 0}

    async with analyzer.acquire() as conn:
        # Per cluster: which entities are already bridged, and to how many faces.
        cluster_entities = await conn.fetch("""
            SELECT f.cluster_id AS cluster_id, ef.entity_id::text AS entity_id, count(*) AS n
            FROM facetracker.faces f
            JOIN public.entity_faces ef ON ef.face_id = f.id
            WHERE f.cluster_id IS NOT NULL AND ef.entity_id IS NOT NULL
            GROUP BY f.cluster_id, ef.entity_id
        """)

        # dominant entity per cluster: exactly one distinct bridged entity.
        by_cluster: dict[int, list[tuple[str, int]]] = {}
        for r in cluster_entities:
            by_cluster.setdefault(r["cluster_id"], []).append((r["entity_id"], r["n"]))

        new_links: list[tuple] = []
        dominant_updates: list[tuple] = []
        for cluster_id, ents in by_cluster.items():
            stats["clusters_with_entity"] += 1
            if len({e for e, _ in ents}) != 1:
                # Multiple bridged entities in one cluster: do NOT propagate
                # (ambiguous). The existing _build_face_match_signals will still
                # emit a cross-entity media_face_match between them — that's the
                # desired same-person evidence, just left to the scorer.
                stats["ambiguous_skipped"] += 1
                continue
            entity_id = ents[0][0]
            dominant_updates.append((entity_id, cluster_id))

            # P1-2 purity anchors: embeddings of the faces in THIS cluster already
            # bridged to the dominant entity. A candidate is only propagated if it
            # is close to one of these anchors (not merely somewhere in the cluster).
            anchor_rows = await conn.fetch("""
                SELECT f.embedding_vec::text AS emb
                FROM facetracker.faces f
                JOIN public.entity_faces ef ON ef.face_id = f.id
                WHERE f.cluster_id = $1 AND ef.entity_id = $2::uuid
                  AND f.embedding_vec IS NOT NULL
            """, cluster_id, entity_id)
            anchors = _normalized_matrix([r["emb"] for r in anchor_rows])

            # Axis-3 Change-1: intra-cluster tightness. If the anchor faces
            # themselves disagree (min pairwise cosine below the tightness
            # threshold), the cluster is a loose single-linkage chain rather
            # than one person. Skip propagation for this cluster entirely.
            if _cluster_too_loose(anchors):
                stats["loose_cluster_skipped"] += 1
                continue

            # Axis-3 Change-1: second-nearest-entity guard. Pull the embeddings
            # of every OTHER bridged entity's face that shares this cluster's
            # neighborhood (via cluster co-membership, the strongest available
            # notion of "nearby in embedding space" without a full kNN scan).
            # If any competitor face lands within cosine >= the guard threshold
            # of an anchor face, the neighborhood is ambiguous and this cluster
            # cannot be safely propagated.
            competitor_rows = await conn.fetch("""
                SELECT f.embedding_vec::text AS emb
                FROM facetracker.faces f
                JOIN public.entity_faces ef ON ef.face_id = f.id
                WHERE f.cluster_id = $1 AND ef.entity_id <> $2::uuid
                  AND f.embedding_vec IS NOT NULL
            """, cluster_id, entity_id)
            if _competing_entity_too_close(anchors, [r["emb"] for r in competitor_rows]):
                stats["competitor_skipped"] += 1
                continue

            # Candidate sibling faces in this cluster that are NOT yet bridged,
            # restricted to low-face-count images (group-photo guard, fail-closed
            # on unknown face_count) and to sufficiently high-quality crops. Flag
            # drive-origin faces (file_path under /mnt/) for the method label.
            candidates = await conn.fetch("""
                SELECT f.id AS face_id, f.embedding_vec::text AS emb,
                       i.file_hash AS file_hash, i.file_path,
                       (i.file_path LIKE '/mnt/%') AS is_drive
                FROM facetracker.faces f
                JOIN facetracker.images i ON i.id = f.image_id
                WHERE f.cluster_id = $1
                  AND COALESCE(i.face_count, $3) <= $2
                  AND COALESCE(f.quality_score, 0) >= $4
                  AND f.embedding_vec IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM public.entity_faces ef WHERE ef.face_id = f.id
                  )
            """, cluster_id, _MAX_PROPAGATE_FACE_COUNT, _UNKNOWN_FACE_COUNT,
                 _PROPAGATE_MIN_QUALITY)

            for c in candidates:
                # Purity gate: max cosine similarity to any anchor must clear the
                # (stricter-than-clustering) propagate threshold. If the dominant
                # entity has no embedded anchor faces we cannot verify purity, so
                # we skip rather than propagate blind.
                if not _passes_purity(c["emb"], anchors):
                    stats["impurity_skipped"] += 1
                    continue
                # media_item_id: collector faces store the media id in file_hash;
                # drive faces store a path sha1 (not a media id) -> leave NULL.
                media_item_id = None if c["is_drive"] else c["file_hash"]
                method = "drive_cross_ref" if c["is_drive"] else "face_cluster"
                new_links.append((entity_id, c["face_id"], media_item_id, 0.6, method))
                stats["propagated"] += 1
                if c["is_drive"]:
                    stats["drive_cross_ref"] += 1

        if new_links:
            async with conn.transaction():
                await conn.executemany("""
                    INSERT INTO public.entity_faces
                        (entity_id, face_id, media_item_id, confidence, method)
                    VALUES ($1::uuid, $2, $3, $4, $5)
                    ON CONFLICT (entity_id, face_id) DO NOTHING
                """, new_links)
        if dominant_updates:
            await conn.executemany(
                "UPDATE facetracker.face_clusters SET dominant_entity_id = $1::uuid WHERE cluster_id = $2",
                dominant_updates,
            )

    return stats


async def propagate_drive_faces_via_knn() -> dict:
    """Axis-3 Change-2: bridge orphan drive faces to entities via direct FAISS
    kNN against bridged collector-face anchors, WITHOUT requiring cluster
    co-membership.

    The cluster-based drive_cross_ref path (in propagate_entity_faces) is
    starved: a drive face only gets attributed when it co-clusters with a
    bridged collector face, which rarely happens across independent scans of
    the drive corpus. This path fires kNN directly against the anchor set, so
    an orphan drive face wins attribution whenever a clear single-entity
    match exists in embedding space.

    Guarded by:
      - top-1 cosine >= _DRIVE_XREF_THRESHOLD (default 0.55)
      - top-1 entity matches top-2 entity, OR top-2 cosine < top-1 - margin
        (default 0.05) — a "clear winner" gate that rejects ambiguous kNN
        neighborhoods (e.g. two entities with faces equally close).

    Confidence is attenuated (top-1 cosine * 0.7) because this is an INDIRECT
    attribution (no platform-owner link on the source image, no co-cluster
    corroboration). Idempotent via ON CONFLICT DO NOTHING on entity_faces.

    Uses FAISS if available; if the import fails we log and return a skipped
    stats row so the phase never hard-fails on a missing optional dep."""
    import time
    _t = time.monotonic()
    stats = {"drive_faces_scanned": 0, "linked": 0, "clear_winner_skipped": 0,
             "below_threshold_skipped": 0, "no_anchors": 0}
    try:
        import faiss  # noqa: F401
    except ImportError:
        logger.warning("faiss unavailable; skipping drive_face_xref")
        return {"skipped": "faiss_unavailable"}

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        # Bridged anchors: every face already linked to an entity, in embedding
        # space. This is the "known people" corpus we kNN against.
        anchor_rows = await conn.fetch("""
            SELECT ef.entity_id::text AS entity_id, f.id AS face_id,
                   f.embedding_vec::text AS emb
            FROM public.entity_faces ef
            JOIN facetracker.faces f ON f.id = ef.face_id
            WHERE f.embedding_vec IS NOT NULL
        """)
        if not anchor_rows:
            stats["no_anchors"] = 1
            return stats

        # Orphan drive faces: no entity_faces row yet. Bounded per pass so a
        # huge orphan pool doesn't monopolise a cycle; the next pass picks up
        # where this one left off (idempotent inserts).
        drive_rows = await conn.fetch("""
            SELECT f.id AS face_id, f.embedding_vec::text AS emb
            FROM facetracker.faces f
            LEFT JOIN public.entity_faces ef ON ef.face_id = f.id
            WHERE ef.face_id IS NULL AND f.embedding_vec IS NOT NULL
            LIMIT $1
        """, _DRIVE_XREF_BATCH)

    stats["drive_faces_scanned"] = len(drive_rows)
    if not drive_rows:
        return stats

    # Build the in-memory FAISS index over anchor embeddings. L2-normalize both
    # sides so inner-product == cosine similarity. This is CPU-heavy on full
    # runs (thousands of drive faces x thousands of anchors), so keep the whole
    # parse/index/search block off the event loop and use HNSW for large anchor
    # corpora. Exact flat search is still used for small deterministic batches.
    anchor_entity_ids = [r["entity_id"] for r in anchor_rows]
    k = min(5, len(anchor_entity_ids))

    def _search():
        A = _normalized_matrix([r["emb"] for r in anchor_rows])
        D = _normalized_matrix([r["emb"] for r in drive_rows])
        if A is None or D is None:
            return None, None
        import faiss
        if _FACE_FAISS_THREADS > 0 and hasattr(faiss, "omp_set_num_threads"):
            faiss.omp_set_num_threads(_FACE_FAISS_THREADS)
        pair_count = len(A) * len(D)
        if len(A) <= _FACE_EXACT_SEARCH_MAX and pair_count <= _DRIVE_XREF_EXACT_PAIR_MAX:
            logger.info(
                "drive_face_xref: using exact FAISS search "
                "(anchors=%d, drive=%d, pairs=%d)",
                len(A), len(D), pair_count,
            )
            index = faiss.IndexFlatIP(A.shape[1])
        else:
            logger.info(
                "drive_face_xref: using FAISS HNSW search "
                "(anchors=%d, drive=%d, pairs=%d, exact_limit=%d, pair_limit=%d, m=%d, ef_search=%d)",
                len(A), len(D), pair_count, _FACE_EXACT_SEARCH_MAX, _DRIVE_XREF_EXACT_PAIR_MAX,
                _FACE_HNSW_M, _FACE_HNSW_EF_SEARCH,
            )
            index = faiss.IndexHNSWFlat(A.shape[1], _FACE_HNSW_M, faiss.METRIC_INNER_PRODUCT)
            index.hnsw.efConstruction = _FACE_HNSW_EF_CONSTRUCTION
            index.hnsw.efSearch = _FACE_HNSW_EF_SEARCH
        index.add(np.ascontiguousarray(A, dtype=np.float32))
        return index.search(np.ascontiguousarray(D, dtype=np.float32), k)

    try:
        sims, idxs = await asyncio.to_thread(_search)
    except Exception:
        logger.warning("drive_face_xref FAISS search failed; skipping phase", exc_info=True)
        return {**stats, "skipped": "faiss_search_failed"}
    if sims is None or idxs is None:
        return stats

    new_links: list[tuple] = []
    for i, row in enumerate(drive_rows):
        top1_sim = float(sims[i, 0])
        if top1_sim < _DRIVE_XREF_THRESHOLD:
            stats["below_threshold_skipped"] += 1
            continue
        top1_entity = anchor_entity_ids[int(idxs[i, 0])]
        # Clear-winner test: prefer top-1 iff its entity matches top-2's entity
        # OR top-2 cosine is at least _DRIVE_XREF_TOP_MARGIN below top-1. This
        # rejects genuine kNN ambiguity (two different entities equally close).
        if k >= 2:
            top2_sim = float(sims[i, 1])
            top2_entity = anchor_entity_ids[int(idxs[i, 1])]
            if top1_entity != top2_entity and top2_sim >= top1_sim - _DRIVE_XREF_TOP_MARGIN:
                stats["clear_winner_skipped"] += 1
                continue
        confidence = top1_sim * 0.7  # attenuate: indirect attribution
        new_links.append((top1_entity, int(row["face_id"]), None,
                          float(confidence), "drive_cross_ref_knn"))

    if new_links:
        async with analyzer.acquire() as conn:
            await conn.executemany("""
                INSERT INTO public.entity_faces
                    (entity_id, face_id, media_item_id, confidence, method)
                VALUES ($1::uuid, $2, $3, $4, $5)
                ON CONFLICT (entity_id, face_id) DO NOTHING
            """, new_links)
        stats["linked"] = len(new_links)

    stats["wall_s"] = round(time.monotonic() - _t, 1)
    logger.info("Drive-face kNN xref: %s", stats)
    return stats


def _uuid_media_ids(media_ids: list[str]) -> list[str]:
    out: list[str] = []
    for media_id in media_ids:
        try:
            out.append(str(UUID(str(media_id))))
        except (TypeError, ValueError):
            continue
    return out


def _chunks(items: list[str], size: int = 5000):
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def _build_entity_lookup(conn) -> dict[tuple[str, str], str]:
    rows = await conn.fetch("""
        SELECT entity_id::text AS eid, source, platform_id, platform_username
        FROM entity_platform_links
        WHERE retracted_at IS NULL
    """)
    lookup: dict[tuple[str, str], str] = {}
    for row in rows:
        source = str(row["source"] or "").lower()
        for value in (row["platform_id"], row["platform_username"]):
            if not value:
                continue
            text = str(value)
            lookup[(source, text)] = row["eid"]
            lookup[(source, text.lower())] = row["eid"]
    return lookup


def _resolve_owner_entity(
    lookup: dict[tuple[str, str], str],
    source: str | None,
    owner: str | None,
) -> str | None:
    if not source or not owner:
        return None
    src = source.lower()
    return lookup.get((src, owner)) or lookup.get((src, owner.lower()))


async def _fetch_indexed_media_ids(*, only_unbridged: bool, max_faces: int | None = None) -> list[str]:
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        await _ensure_schema(conn)
        rows = await conn.fetch("""
            SELECT DISTINCT i.file_hash AS media_item_id
            FROM facetracker.images i
            JOIN facetracker.faces f ON f.image_id = i.id
            WHERE i.file_hash ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
              AND f.embedding_vec IS NOT NULL
              AND NOT COALESCE(f.is_junk, FALSE)
              AND ($1::int IS NULL OR $1 <= 0 OR COALESCE(i.face_count, $2) <= $1)
              AND (
                    NOT $3::bool
                    OR NOT EXISTS (SELECT 1 FROM public.entity_faces ef WHERE ef.face_id = f.id)
                  )
        """, max_faces, _UNKNOWN_FACE_COUNT, only_unbridged)
    return [r["media_item_id"] for r in rows if r["media_item_id"]]


async def _fetch_collector_media_owners(media_ids: list[str], *, profile_only: bool) -> list[dict]:
    uuid_ids = _uuid_media_ids(media_ids)
    if not uuid_ids:
        return []

    collector = get_collector_pool()
    rows: list[dict] = []
    profile_content_types = ["profile_photo", "avatar", "profile_pic"]
    profile_kinds = ["profile", "profile_photo", "avatar"]
    async with collector.acquire() as conn:
        for chunk in _chunks(uuid_ids):
            if profile_only:
                records = await conn.fetch("""
                    SELECT id::text AS media_item_id, source, entity_id, content_type, kind
                    FROM media_items
                    WHERE id = ANY($1::uuid[])
                      AND NULLIF(entity_id, '') IS NOT NULL
                      AND (
                            content_type = ANY($2::text[])
                            OR kind = ANY($3::text[])
                          )
                """, chunk, profile_content_types, profile_kinds)
            else:
                records = await conn.fetch("""
                    SELECT id::text AS media_item_id, source, entity_id, content_type, kind
                    FROM media_items
                    WHERE id = ANY($1::uuid[])
                      AND NULLIF(entity_id, '') IS NOT NULL
                """, chunk)
            rows.extend(dict(r) for r in records)
    return rows


async def _upsert_media_owner_face_links(
    owner_rows: list[dict],
    *,
    method: str,
    confidence: float,
    update_existing: bool,
) -> dict:
    if not owner_rows:
        return {"media_resolved": 0, "linked": 0, "updated": 0}

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        lookup = await _build_entity_lookup(conn)
        records_by_media: dict[str, tuple[str, str, float]] = {}
        for row in owner_rows:
            entity_id = _resolve_owner_entity(
                lookup,
                row.get("source"),
                row.get("entity_id"),
            )
            if entity_id:
                records_by_media[str(row["media_item_id"])] = (
                    str(row["media_item_id"]),
                    entity_id,
                    confidence,
                )
        records = list(records_by_media.values())
        if not records:
            return {"media_resolved": 0, "linked": 0, "updated": 0}

        async with conn.transaction():
            await conn.execute("""
                CREATE TEMP TABLE tmp_face_media_owners (
                    media_item_id TEXT PRIMARY KEY,
                    entity_id     TEXT NOT NULL,
                    confidence    FLOAT8 NOT NULL
                ) ON COMMIT DROP
            """)
            await conn.copy_records_to_table(
                "tmp_face_media_owners",
                records=records,
                columns=["media_item_id", "entity_id", "confidence"],
            )

            updated = 0
            if update_existing:
                updated = await conn.fetchval("""
                    WITH candidates AS (
                        SELECT DISTINCT
                               t.entity_id::uuid AS entity_id,
                               f.id              AS face_id,
                               t.media_item_id,
                               t.confidence
                        FROM tmp_face_media_owners t
                        JOIN facetracker.images i ON i.file_hash = t.media_item_id
                        JOIN facetracker.faces f ON f.image_id = i.id
                        WHERE f.embedding_vec IS NOT NULL
                          AND NOT COALESCE(f.is_junk, FALSE)
                    ),
                    changed AS (
                        UPDATE public.entity_faces ef
                        SET method = $1,
                            confidence = GREATEST(COALESCE(ef.confidence, 0), c.confidence),
                            media_item_id = COALESCE(ef.media_item_id, c.media_item_id)
                        FROM candidates c
                        WHERE ef.entity_id = c.entity_id
                          AND ef.face_id = c.face_id
                          AND (
                                ef.method IS DISTINCT FROM $1
                                OR COALESCE(ef.confidence, 0) < c.confidence
                              )
                        RETURNING 1
                    )
                    SELECT count(*) FROM changed
                """, method) or 0

            linked = await conn.fetchval("""
                WITH candidates AS (
                    SELECT DISTINCT
                           t.entity_id::uuid AS entity_id,
                           f.id              AS face_id,
                           t.media_item_id,
                           t.confidence
                    FROM tmp_face_media_owners t
                    JOIN facetracker.images i ON i.file_hash = t.media_item_id
                    JOIN facetracker.faces f ON f.image_id = i.id
                    WHERE f.embedding_vec IS NOT NULL
                      AND NOT COALESCE(f.is_junk, FALSE)
                      AND NOT EXISTS (
                          SELECT 1 FROM public.entity_faces existing
                          WHERE existing.face_id = f.id
                      )
                ),
                inserted AS (
                    INSERT INTO public.entity_faces
                        (entity_id, face_id, media_item_id, confidence, method)
                    SELECT entity_id, face_id, media_item_id, confidence, $1
                    FROM candidates
                    ON CONFLICT (entity_id, face_id) DO NOTHING
                    RETURNING 1
                )
                SELECT count(*) FROM inserted
            """, method) or 0

    return {"media_resolved": len(records), "linked": int(linked), "updated": int(updated)}


async def bridge_faces_by_cluster_propagation() -> dict:
    """Propagate a single, uncontested entity label across each face cluster.

    This is intentionally broader than the older group-photo-guarded
    propagate_entity_faces(): once clustering has put faces in the same person
    component, any cluster with exactly one bridged entity becomes an attribution
    source for every still-unbridged non-junk face in that cluster. Clusters
    already containing >1 bridged entity are left untouched and counted.
    """
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        await _ensure_schema(conn)
        row = await conn.fetchrow("""
            WITH cluster_entities AS (
                SELECT f.cluster_id,
                       count(DISTINCT ef.entity_id) AS entity_count,
                       min(ef.entity_id::text)      AS entity_id
                FROM facetracker.faces f
                JOIN public.entity_faces ef ON ef.face_id = f.id
                WHERE f.cluster_id IS NOT NULL
                  AND NOT COALESCE(f.is_junk, FALSE)
                GROUP BY f.cluster_id
            ),
            eligible AS (
                SELECT cluster_id, entity_id
                FROM cluster_entities
                WHERE entity_count = 1
            ),
            contested AS (
                SELECT cluster_id
                FROM cluster_entities
                WHERE entity_count > 1
            ),
            dominant AS (
                UPDATE facetracker.face_clusters fc
                SET dominant_entity_id = e.entity_id::uuid
                FROM eligible e
                WHERE fc.cluster_id = e.cluster_id
                RETURNING fc.cluster_id
            ),
            candidates AS (
                SELECT DISTINCT
                       e.entity_id::uuid AS entity_id,
                       f.id              AS face_id,
                       CASE
                         WHEN i.file_hash ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                         THEN i.file_hash
                         ELSE NULL
                       END AS media_item_id
                FROM eligible e
                JOIN facetracker.faces f ON f.cluster_id = e.cluster_id
                JOIN facetracker.images i ON i.id = f.image_id
                WHERE f.embedding_vec IS NOT NULL
                  AND NOT COALESCE(f.is_junk, FALSE)
                  AND NOT EXISTS (
                      SELECT 1 FROM public.entity_faces existing
                      WHERE existing.face_id = f.id
                  )
            ),
            inserted AS (
                INSERT INTO public.entity_faces
                    (entity_id, face_id, media_item_id, confidence, method)
                SELECT entity_id, face_id, media_item_id, 0.90, 'cluster_propagation'
                FROM candidates
                ON CONFLICT (entity_id, face_id) DO NOTHING
                RETURNING 1
            )
            SELECT
                (SELECT count(*) FROM eligible)   AS clusters_with_single_entity,
                (SELECT count(*) FROM contested)  AS contested_clusters,
                (SELECT count(*) FROM candidates) AS candidate_faces,
                (SELECT count(*) FROM inserted)   AS linked
        """)
    stats = dict(row)
    logger.info("face bridge cluster_propagation: %s", stats)
    return stats


async def bridge_profile_photo_faces() -> dict:
    try:
        media_ids = await _fetch_indexed_media_ids(only_unbridged=False)
        owner_rows = await _fetch_collector_media_owners(media_ids, profile_only=True)
    except Exception as exc:  # noqa: BLE001 - collector owner context is optional
        if is_collector_unavailable_error(exc):
            logger.warning("face bridge profile_photo skipped: collector unavailable (%s)", exc)
            return {"collector_skipped": True, "linked": 0, "updated": 0}
        raise

    stats = await _upsert_media_owner_face_links(
        owner_rows,
        method="profile_photo",
        confidence=0.95,
        update_existing=True,
    )
    stats["media_candidates"] = len(media_ids)
    stats["collector_rows"] = len(owner_rows)
    logger.info("face bridge profile_photo: %s", stats)
    return stats


async def bridge_media_owner_faces() -> dict:
    try:
        media_ids = await _fetch_indexed_media_ids(
            only_unbridged=True,
            max_faces=_FACE_MEDIA_ATTRIBUTION_MAX_FACES,
        )
        owner_rows = await _fetch_collector_media_owners(media_ids, profile_only=False)
    except Exception as exc:  # noqa: BLE001 - collector owner context is optional
        if is_collector_unavailable_error(exc):
            logger.warning("face bridge media_attribution skipped: collector unavailable (%s)", exc)
            return {"collector_skipped": True, "linked": 0, "updated": 0}
        raise

    stats = await _upsert_media_owner_face_links(
        owner_rows,
        method="media_attribution",
        confidence=0.65,
        update_existing=False,
    )
    stats["media_candidates"] = len(media_ids)
    stats["collector_rows"] = len(owner_rows)
    stats["max_faces_per_media"] = _FACE_MEDIA_ATTRIBUTION_MAX_FACES
    logger.info("face bridge media_attribution: %s", stats)
    return stats


async def bridge_faces_via_knn_propagation() -> dict:
    analyzer = get_analyzer_pool()
    stats = {
        "threshold": _FACE_KNN_PROPAGATION_THRESHOLD,
        "faces_scanned": 0,
        "linked": 0,
        "no_anchors": 0,
    }
    async with analyzer.acquire() as conn:
        await _ensure_schema(conn)
        anchor_count = await conn.fetchval("""
            SELECT count(*)
            FROM public.entity_faces ef
            JOIN facetracker.faces f ON f.id = ef.face_id
            WHERE f.embedding_vec IS NOT NULL
              AND NOT COALESCE(f.is_junk, FALSE)
        """) or 0
        if anchor_count == 0:
            stats["no_anchors"] = 1
            return stats

        async with conn.transaction():
            await conn.execute("SET LOCAL enable_seqscan = off")
            await conn.execute("SET LOCAL ivfflat.probes = 10")
            row = await conn.fetchrow("""
                WITH cluster_entity_counts AS (
                    SELECT f.cluster_id, count(DISTINCT ef.entity_id) AS entity_count
                    FROM facetracker.faces f
                    JOIN public.entity_faces ef ON ef.face_id = f.id
                    WHERE f.cluster_id IS NOT NULL
                      AND NOT COALESCE(f.is_junk, FALSE)
                    GROUP BY f.cluster_id
                ),
                targets AS (
                    SELECT f.id AS face_id,
                           f.embedding_vec,
                           CASE
                             WHEN i.file_hash ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                             THEN i.file_hash
                             ELSE NULL
                           END AS media_item_id
                    FROM facetracker.faces f
                    JOIN facetracker.images i ON i.id = f.image_id
                    LEFT JOIN cluster_entity_counts target_cec
                      ON target_cec.cluster_id = f.cluster_id
                    WHERE f.embedding_vec IS NOT NULL
                      AND NOT COALESCE(f.is_junk, FALSE)
                      -- KNN only seeds clusters with no bridged entity yet.
                      -- Clusters with one entity are handled by cluster_propagation;
                      -- clusters with multiple entities are contested and unsafe.
                      AND COALESCE(target_cec.entity_count, 0) = 0
                      AND NOT EXISTS (
                          SELECT 1 FROM public.entity_faces existing
                          WHERE existing.face_id = f.id
                      )
                    ORDER BY f.quality_score DESC NULLS LAST
                    LIMIT $2
                ),
                nearest AS (
                    SELECT t.face_id,
                           t.media_item_id,
                           nn.entity_id,
                           nn.similarity
                    FROM targets t
                    CROSS JOIN LATERAL (
                        SELECT ef.entity_id,
                               1 - (anchor.embedding_vec <=> t.embedding_vec) AS similarity
                        FROM public.entity_faces ef
                        JOIN facetracker.faces anchor ON anchor.id = ef.face_id
                        LEFT JOIN cluster_entity_counts anchor_cec
                          ON anchor_cec.cluster_id = anchor.cluster_id
                        WHERE anchor.embedding_vec IS NOT NULL
                          AND NOT COALESCE(anchor.is_junk, FALSE)
                          -- Do not propagate from ambiguous identity neighborhoods.
                          AND (anchor.cluster_id IS NULL OR anchor_cec.entity_count = 1)
                        ORDER BY anchor.embedding_vec <=> t.embedding_vec
                        LIMIT 1
                    ) nn
                    WHERE nn.similarity >= $1
                ),
                inserted AS (
                    INSERT INTO public.entity_faces
                        (entity_id, face_id, media_item_id, confidence, method)
                    SELECT entity_id, face_id, media_item_id, similarity, 'knn_propagation'
                    FROM nearest
                    ON CONFLICT (entity_id, face_id) DO NOTHING
                    RETURNING 1
                )
                SELECT
                    (SELECT count(*) FROM targets)  AS faces_scanned,
                    (SELECT count(*) FROM inserted) AS linked
            """, _FACE_KNN_PROPAGATION_THRESHOLD, _FACE_KNN_PROPAGATION_BATCH)
    stats.update(dict(row))
    logger.info("face bridge knn_propagation: %s", stats)
    return stats


async def bridge_faces_to_entities() -> dict:
    cluster = await bridge_faces_by_cluster_propagation()
    profile = await bridge_profile_photo_faces()
    media = await bridge_media_owner_faces()
    try:
        knn = await bridge_faces_via_knn_propagation()
    except (TimeoutError, QueryCanceledError) as exc:
        logger.warning("face bridge knn_propagation skipped: %s", exc.__class__.__name__)
        knn = {
            "threshold": _FACE_KNN_PROPAGATION_THRESHOLD,
            "faces_scanned": 0,
            "linked": 0,
            "skipped": True,
            "error": exc.__class__.__name__,
        }

    linked_total = (
        int(cluster.get("linked") or 0)
        + int(profile.get("linked") or 0)
        + int(media.get("linked") or 0)
        + int(knn.get("linked") or 0)
    )
    updated_total = int(profile.get("updated") or 0) + int(media.get("updated") or 0)
    return {
        "bridge_cluster_propagation": cluster,
        "bridge_profile_photo": profile,
        "bridge_media_attribution": media,
        "bridge_knn_propagation": knn,
        "bridge_linked_total": linked_total,
        "bridge_updated_total": updated_total,
    }


# ── Q2: cluster -> identity materialization ──

# A cluster must have at least this many non-junk faces to become an identity.
# Singletons (size 1) are almost always one-off detections; requiring >=2 keeps
# facetracker.identities meaningful (a "person seen more than once").
_IDENTITY_MIN_CLUSTER_SIZE = int(os.getenv("FACE_IDENTITY_MIN_CLUSTER_SIZE", "2"))


async def build_identities_from_clusters() -> dict:
    """Q2: materialize facetracker.identities (+ face_identity_map) from the
    face clusters produced by cluster_faces().

    Root cause this fixes: nothing in the codebase ever wrote facetracker.identities
    (face_worker Stage-1 only creates the schema), so identities stayed 0 even
    with 10k+ clustered faces. This builds one Identity per multi-face cluster,
    computes its centroid embedding, maps every non-junk face in the cluster to it
    (face_identity_map, assigned_by='auto_cluster'), and — when the cluster has a
    single dominant bridged entity — copies that entity's canonical_name onto the
    Identity.name so the identity is human-readable.

    Idempotent: rebuilt from scratch each pass (identities are a pure projection
    of the current clustering). face_identity_map is keyed unique on face_id.

    Returns counts. Junk faces (is_junk) are excluded."""
    import time
    _t = time.monotonic()
    analyzer = get_analyzer_pool()
    stats = {"identities": 0, "faces_mapped": 0, "named": 0}

    async with analyzer.acquire() as conn:
        await _ensure_schema(conn)
        # Clusters eligible to become identities: size >= min, non-junk faces only.
        clusters = await conn.fetch(
            """
            SELECT cluster_id, count(*) AS n
            FROM facetracker.faces
            WHERE cluster_id IS NOT NULL AND NOT is_junk AND embedding_vec IS NOT NULL
            GROUP BY cluster_id
            HAVING count(*) >= $1
            """,
            _IDENTITY_MIN_CLUSTER_SIZE,
        )
        if not clusters:
            logger.info("build_identities_from_clusters: no eligible clusters")
            return stats

        # Dominant bridged entity per cluster (for naming) — single distinct
        # entity only, else leave unnamed (ambiguous).
        ent_rows = await conn.fetch(
            """
            SELECT f.cluster_id, ef.entity_id::text AS eid, count(*) AS n
            FROM facetracker.faces f
            JOIN public.entity_faces ef ON ef.face_id = f.id
            WHERE f.cluster_id IS NOT NULL AND NOT f.is_junk
            GROUP BY f.cluster_id, ef.entity_id
            """
        )
        by_cluster: dict[int, set[str]] = {}
        for r in ent_rows:
            by_cluster.setdefault(r["cluster_id"], set()).add(r["eid"])
        dominant = {cid: next(iter(es)) for cid, es in by_cluster.items() if len(es) == 1}
        # Resolve canonical names for the dominant entities in one shot.
        names: dict[str, str] = {}
        if dominant:
            name_rows = await conn.fetch(
                "SELECT id::text AS id, canonical_name FROM entities WHERE id = ANY($1::uuid[])",
                list(set(dominant.values())),
            )
            names = {r["id"]: r["canonical_name"] for r in name_rows if r["canonical_name"]}

        # Rebuild from scratch (pure projection of current clustering).
        async with conn.transaction():
            await conn.execute("TRUNCATE facetracker.face_identity_map, facetracker.identities RESTART IDENTITY CASCADE")
            for c in clusters:
                cid = c["cluster_id"]
                eid = dominant.get(cid)
                nm = names.get(eid) if eid else None
                # Centroid = mean of the cluster's non-junk embeddings (computed
                # in SQL via pgvector AVG). label mirrors name for the UI.
                centroid = await conn.fetchval(
                    """
                    SELECT AVG(embedding_vec)::text
                    FROM facetracker.faces
                    WHERE cluster_id = $1 AND NOT is_junk AND embedding_vec IS NOT NULL
                    """,
                    cid,
                )
                identity_id = await conn.fetchval(
                    """
                    INSERT INTO facetracker.identities (name, label, is_verified, cluster_id, centroid_embedding)
                    VALUES ($1, $1, FALSE, $2, $3::vector)
                    RETURNING id
                    """,
                    nm, cid, centroid,
                )
                stats["identities"] += 1
                if nm:
                    stats["named"] += 1
                # Map every non-junk face in the cluster to this identity. The
                # highest-quality face is the primary representative.
                mapped = await conn.execute(
                    """
                    INSERT INTO facetracker.face_identity_map
                        (face_id, identity_id, similarity_to_centroid, is_primary, assigned_by, confidence)
                    SELECT f.id, $2, NULL,
                           (f.id = (SELECT id FROM facetracker.faces
                                    WHERE cluster_id = $1 AND NOT is_junk AND embedding_vec IS NOT NULL
                                    ORDER BY quality_score DESC NULLS LAST LIMIT 1)),
                           'auto_cluster', f.quality_score
                    FROM facetracker.faces f
                    WHERE f.cluster_id = $1 AND NOT f.is_junk AND f.embedding_vec IS NOT NULL
                    ON CONFLICT (face_id) DO NOTHING
                    """,
                    cid, identity_id,
                )
                # asyncpg execute returns "INSERT 0 N"; parse the count.
                try:
                    stats["faces_mapped"] += int(str(mapped).split()[-1])
                except (ValueError, IndexError):
                    pass

    stats["wall_s"] = round(time.monotonic() - _t, 1)
    logger.info("build_identities_from_clusters: %s", stats)
    return stats


async def run_face_clustering() -> dict:
    """Orchestrator: cluster faces then propagate entity attribution. Gated on
    face-corpus growth so idle cycles are cheap. Wired into incremental_runner
    BEFORE rebuild_face_match_signals (which then emits media_face_match from the
    enriched entity_faces). Returns -1-style skip via stats['skipped']."""
    global _last_face_count
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        await _ensure_schema(conn)
        face_count = await conn.fetchval(
            "SELECT count(*) FROM facetracker.faces WHERE embedding_vec IS NOT NULL"
        )
    if face_count == _last_face_count:
        bridge_stats = await bridge_faces_to_entities()
        if bridge_stats.get("bridge_linked_total") or bridge_stats.get("bridge_updated_total"):
            ident_stats = await build_identities_from_clusters()
            stats = {
                "cluster_skipped": "no_new_faces",
                "faces": face_count,
                **bridge_stats,
                "identities": ident_stats.get("identities"),
                "identities_named": ident_stats.get("named"),
            }
            logger.info("Face clustering bridge-only refresh: %s", stats)
            return stats
        return {"skipped": "no_new_faces", "faces": face_count, **bridge_stats}

    # Q3: flag obvious non-faces BEFORE clustering so junk never joins a cluster
    # or bridges to an entity. Cheap idempotent UPDATE; re-runs each growth tick.
    junk_stats = await flag_junk_faces()

    cluster_stats = await cluster_faces()
    if cluster_stats.get("skipped"):
        _last_face_count = face_count
        return cluster_stats
    bridge_stats = await bridge_faces_to_entities()
    # Q2: materialize identities from the (now-propagated) clusters. Runs after
    # propagation so newly-bridged entities can name their identity.
    ident_stats = await build_identities_from_clusters()
    _last_face_count = face_count

    stats = {**cluster_stats, **bridge_stats,
             "junk_flagged": junk_stats.get("junk_after"),
             "identities": ident_stats.get("identities"),
             "identities_named": ident_stats.get("named")}
    logger.info("Face clustering: %s", stats)
    return stats
