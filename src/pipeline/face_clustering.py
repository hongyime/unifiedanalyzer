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
# Max faces to cluster per pass (top-N by quality). Keeps the O(n^2) distance
# matrix bounded; raise once a kNN-graph backend replaces agglomerative.
_FACE_CLUSTER_MAX = int(os.getenv("FACE_CLUSTER_MAX", "20000"))
# Group-photo guard: only propagate attribution to sibling faces from images
# with at most this many detected faces (selfies/portraits, not crowds).
_MAX_PROPAGATE_FACE_COUNT = int(os.getenv("FACE_CLUSTER_MAX_GROUP_FACES", "3"))

# Internal gate: skip the whole pass when the face corpus hasn't grown.
_last_face_count = -1


async def _ensure_schema(conn) -> None:
    """Idempotent DDL: a cluster_id on faces + a cluster metadata table."""
    await conn.execute("ALTER TABLE facetracker.faces ADD COLUMN IF NOT EXISTS cluster_id INTEGER")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_cluster ON facetracker.faces(cluster_id)")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS facetracker.face_clusters (
            cluster_id          INTEGER PRIMARY KEY,
            size                INTEGER NOT NULL,
            dominant_entity_id  UUID,
            created_at          TIMESTAMPTZ DEFAULT NOW()
        )
    """)


async def cluster_faces() -> dict:
    """Cluster facetracker.faces by embedding; write cluster_id back. Returns
    stats. Uses average-linkage agglomerative clustering on cosine distance
    (average linkage avoids the single-linkage chaining that merges distinct
    people through a borderline bridge face)."""
    from sklearn.cluster import AgglomerativeClustering

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        await _ensure_schema(conn)
        rows = await conn.fetch("""
            SELECT id, embedding_vec::text AS emb
            FROM facetracker.faces
            WHERE embedding_vec IS NOT NULL
            ORDER BY quality_score DESC NULLS LAST
            LIMIT $1
        """, _FACE_CLUSTER_MAX)

    if len(rows) < 2:
        return {"faces": len(rows), "clusters": 0, "skipped": "too_few_faces"}

    face_ids = [r["id"] for r in rows]
    M = np.asarray([json.loads(r["emb"]) for r in rows], dtype=np.float32)
    # Normalize so cosine distance = 1 - dot.
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    M /= norms

    labels = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=1.0 - _CLUSTER_THRESHOLD,
    ).fit_predict(M)

    # Persist assignments + cluster sizes.
    sizes: dict[int, int] = {}
    assignments = []
    for fid, lab in zip(face_ids, labels):
        lab = int(lab)
        sizes[lab] = sizes.get(lab, 0) + 1
        assignments.append((lab, fid))

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

    multi = sum(1 for s in sizes.values() if s > 1)
    return {"faces": len(face_ids), "clusters": len(sizes), "multi_face_clusters": multi}


async def propagate_entity_faces() -> dict:
    """For each cluster with a single unambiguous dominant entity, bridge the
    cluster's currently-unbridged faces to that entity (group-photo guarded).
    Grows public.entity_faces so the existing media_face_match builder fires.

    Returns stats incl. how many drive-origin faces got cross-referenced.
    """
    analyzer = get_analyzer_pool()
    stats = {"clusters_with_entity": 0, "propagated": 0, "drive_cross_ref": 0, "ambiguous_skipped": 0}

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

            # Candidate sibling faces in this cluster that are NOT yet bridged,
            # restricted to low-face-count images (group-photo guard). Flag
            # drive-origin faces (file_path under /mnt/) for the method label.
            candidates = await conn.fetch("""
                SELECT f.id AS face_id, i.file_hash AS file_hash, i.file_path,
                       (i.file_path LIKE '/mnt/%') AS is_drive
                FROM facetracker.faces f
                JOIN facetracker.images i ON i.id = f.image_id
                WHERE f.cluster_id = $1
                  AND COALESCE(i.face_count, 1) <= $2
                  AND NOT EXISTS (
                      SELECT 1 FROM public.entity_faces ef WHERE ef.face_id = f.id
                  )
            """, cluster_id, _MAX_PROPAGATE_FACE_COUNT)

            for c in candidates:
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

    cluster_stats = await cluster_faces()
    if cluster_stats.get("skipped"):
        _last_face_count = face_count
        return cluster_stats
    prop_stats = await propagate_entity_faces()
    _last_face_count = face_count

    stats = {**cluster_stats, **prop_stats}
    logger.info("Face clustering: %s", stats)
    return stats
