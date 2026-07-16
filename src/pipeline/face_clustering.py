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

import numpy as np

from src.db.connection import get_analyzer_pool

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
_DRIVE_XREF_THRESHOLD = float(os.getenv("FACE_DRIVE_XREF_THRESHOLD", "0.55"))
_DRIVE_XREF_TOP_MARGIN = float(os.getenv("FACE_DRIVE_XREF_TOP_MARGIN", "0.05"))
_DRIVE_XREF_BATCH = int(os.getenv("FACE_DRIVE_XREF_BATCH", "5000"))

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
        index = faiss.IndexFlatIP(M.shape[1])  # inner product on normalized = cosine
        index.add(M)
        sims, idxs = index.search(M, k)
    except Exception:
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
    # sides so IndexFlatIP inner-product == cosine similarity.
    anchor_entity_ids = [r["entity_id"] for r in anchor_rows]
    A = _normalized_matrix([r["emb"] for r in anchor_rows])
    D = _normalized_matrix([r["emb"] for r in drive_rows])
    if A is None or D is None:
        return stats

    import faiss  # confirmed importable above
    index = faiss.IndexFlatIP(A.shape[1])
    index.add(np.ascontiguousarray(A, dtype=np.float32))
    k = min(5, len(A))
    sims, idxs = index.search(np.ascontiguousarray(D, dtype=np.float32), k)

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

    logger.info("Drive-face kNN xref: %s", stats)
    return stats


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
        return {"skipped": "no_new_faces", "faces": face_count}

    # Q3: flag obvious non-faces BEFORE clustering so junk never joins a cluster
    # or bridges to an entity. Cheap idempotent UPDATE; re-runs each growth tick.
    junk_stats = await flag_junk_faces()

    cluster_stats = await cluster_faces()
    if cluster_stats.get("skipped"):
        _last_face_count = face_count
        return cluster_stats
    prop_stats = await propagate_entity_faces()
    # Q2: materialize identities from the (now-propagated) clusters. Runs after
    # propagation so newly-bridged entities can name their identity.
    ident_stats = await build_identities_from_clusters()
    _last_face_count = face_count

    stats = {**cluster_stats, **prop_stats,
             "junk_flagged": junk_stats.get("junk_after"),
             "identities": ident_stats.get("identities"),
             "identities_named": ident_stats.get("named")}
    logger.info("Face clustering: %s", stats)
    return stats
