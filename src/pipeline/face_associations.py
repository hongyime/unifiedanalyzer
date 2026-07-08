"""Face social graph — face_associations builder (2026-07-08).

Vision: when entity A posts a photo with N detected faces (N >= 2), A's own face
plus N-1 friends' faces appear in that image. This module walks every collector
media item A owns, matches A's *primary face* against the N detected faces, and
records the remaining N-1 faces as A's `face_associations`. The downstream
`social_face_link` phase (src/pipeline/social_face_link.py) then emits an
identity_signals row whenever entity B's primary face matches any face in A's
face_associations at cosine >= 0.55 — either B is in A's social circle, or B is
A viewed via a friend's photo.

DESIGN (agreed):
1. Primary face for an entity =
     * Priority 1: highest-quality face bridged via public.entity_faces
       (method='media_attribution') from a media_items whose content_type is
       'profile_photo'. Stored to entities.primary_face_id.
     * Fallback: largest bridged cluster's highest-quality face (approximation
       of the cluster centroid — good enough as a representative face).
2. Face associations: for each collector media item where face_count >= 2 owned
   by entity A whose primary_face_id is set, if A's primary face embedding has
   cosine >= 0.55 to at least one detected face in that image, mark THAT face
   as A's-own and treat all OTHER faces in the image as A's associates.

STORAGE: public.face_associations, unique on
(entity_id, associated_face_id, media_item_id). Idempotent inserts via
ON CONFLICT DO NOTHING so repeated passes just top up the graph.

IDLE-SKIP: the pass no-ops when the face corpus hasn't grown since the previous
call, matching the `_last_face_count` pattern in face_clustering.run_face_clustering.

BOUNDED: FACE_ASSOCIATIONS_MAX_MEDIA per pass (default 2000) — the next pass
picks up where this one left off (ON CONFLICT DO NOTHING keeps it safe).
"""
import asyncio
import json
import logging
import os

import numpy as np

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

# Cosine >= this counts as "A's own face is among these detected faces" (used
# to identify A's-own face within an N-face image so the OTHER N-1 become
# associates). Same 0.55 threshold as social_face_link + the propagation-purity
# gate in face_clustering.
_ASSOC_THRESHOLD = float(os.getenv("FACE_ASSOCIATIONS_THRESHOLD", "0.55"))
_MAX_MEDIA = int(os.getenv("FACE_ASSOCIATIONS_MAX_MEDIA", "2000"))

# Idle-skip gate: don't re-run when the face corpus hasn't grown since the last
# successful pass. Mirrors face_clustering._last_face_count.
_last_face_count = -1


def _normalize_embedding(emb_text: str) -> "np.ndarray | None":
    """Parse a pgvector-text embedding into a unit-norm float32 vector.
    Returns None on empty / zero-norm inputs so callers can skip cleanly."""
    v = np.asarray(json.loads(emb_text), dtype=np.float32)
    n = float(np.linalg.norm(v))
    if n == 0.0:
        return None
    return v / n


async def _resolve_primary_faces() -> dict[str, tuple[int, "np.ndarray"]]:
    """Resolve each entity's primary_face_id and persist to entities. Returns
    {entity_id_text: (face_id, normalized_embedding)}.

    Priority 1: face bridged via entity_faces (method='media_attribution') from
    a media_items where content_type='profile_photo', highest quality first.
    Fallback: highest-quality face in the entity's LARGEST bridged cluster.
    """
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    # Priority 1 anchors: profile_photo media_item_ids live in the collector DB.
    # One fetch batches every profile photo across all entities.
    async with collector.acquire() as conn:
        pp_rows = await conn.fetch(
            "SELECT id::text AS mid FROM media_items WHERE content_type = 'profile_photo'"
        )
    profile_photo_ids = [r["mid"] for r in pp_rows]

    primary: dict[str, tuple[int, str]] = {}  # entity_id -> (face_id, emb_text)

    async with analyzer.acquire() as conn:
        if profile_photo_ids:
            # DISTINCT ON (entity_id) with ORDER BY quality DESC → one highest-
            # quality profile-photo face per entity in a single query.
            rows = await conn.fetch("""
                SELECT DISTINCT ON (ef.entity_id)
                       ef.entity_id::text AS entity_id,
                       f.id               AS face_id,
                       f.embedding_vec::text AS emb
                FROM public.entity_faces ef
                JOIN facetracker.faces f ON f.id = ef.face_id
                WHERE ef.method = 'media_attribution'
                  AND ef.media_item_id = ANY($1::text[])
                  AND f.embedding_vec IS NOT NULL
                ORDER BY ef.entity_id, f.quality_score DESC NULLS LAST
            """, profile_photo_ids)
            for r in rows:
                primary[r["entity_id"]] = (int(r["face_id"]), r["emb"])

        # Fallback: for each entity with bridged faces but NO priority-1 hit,
        # find its largest cluster (most bridged faces of THIS entity) and pick
        # the highest-quality face in that cluster as the representative.
        candidate_rows = await conn.fetch("""
            SELECT DISTINCT ef.entity_id::text AS entity_id
            FROM public.entity_faces ef
            JOIN facetracker.faces f ON f.id = ef.face_id
            WHERE ef.entity_id IS NOT NULL AND f.embedding_vec IS NOT NULL
        """)
        needing = [r["entity_id"] for r in candidate_rows if r["entity_id"] not in primary]

        if needing:
            fb_rows = await conn.fetch("""
                WITH bridged AS (
                    SELECT ef.entity_id, f.id AS face_id, f.cluster_id,
                           f.quality_score, f.embedding_vec
                    FROM public.entity_faces ef
                    JOIN facetracker.faces f ON f.id = ef.face_id
                    WHERE ef.entity_id = ANY($1::uuid[])
                      AND f.cluster_id IS NOT NULL
                      AND f.embedding_vec IS NOT NULL
                ),
                cluster_sizes AS (
                    SELECT entity_id, cluster_id, count(*) AS n
                    FROM bridged GROUP BY entity_id, cluster_id
                ),
                largest AS (
                    SELECT DISTINCT ON (entity_id) entity_id, cluster_id
                    FROM cluster_sizes ORDER BY entity_id, n DESC
                )
                SELECT DISTINCT ON (l.entity_id)
                       l.entity_id::text AS entity_id,
                       b.face_id         AS face_id,
                       b.embedding_vec::text AS emb
                FROM largest l
                JOIN bridged b
                    ON b.entity_id = l.entity_id AND b.cluster_id = l.cluster_id
                ORDER BY l.entity_id, b.quality_score DESC NULLS LAST
            """, needing)
            for r in fb_rows:
                primary[r["entity_id"]] = (int(r["face_id"]), r["emb"])

        # Persist primary_face_id on entities so the social_face_link phase (and
        # any dashboard code) can query it without re-running this resolution.
        if primary:
            updates = [(fid, eid) for eid, (fid, _emb) in primary.items()]
            await conn.executemany(
                "UPDATE entities SET primary_face_id = $1 WHERE id = $2::uuid",
                updates,
            )

    # L2-normalize once, off the event loop. Downstream cosine work is a single
    # dot product per face.
    def _normalize_all(items):
        out: dict[str, tuple[int, np.ndarray]] = {}
        for eid, (fid, emb_text) in items:
            v = _normalize_embedding(emb_text)
            if v is not None:
                out[eid] = (fid, v)
        return out

    return await asyncio.to_thread(_normalize_all, list(primary.items()))


async def _fetch_owned_multiface_media(
    entity_ids: list[str],
) -> list[dict]:
    """Owned collector media (via method='media_attribution') where the source
    image has face_count >= 2. Distinct (entity_id, media_item_id, image_id)
    triples — one entity typically owns one media, but the query tolerates the
    edge case of multiple bridged entities per media (each processed once).
    Bounded by _MAX_MEDIA; the next pass picks up the rest."""
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT
                   ef.entity_id::text  AS entity_id,
                   ef.media_item_id    AS mid,
                   f.image_id          AS image_id
            FROM public.entity_faces ef
            JOIN facetracker.faces f  ON f.id = ef.face_id
            JOIN facetracker.images i ON i.id = f.image_id
            WHERE ef.method = 'media_attribution'
              AND ef.entity_id = ANY($1::uuid[])
              AND ef.media_item_id IS NOT NULL
              AND i.face_count >= 2
            LIMIT $2
        """, entity_ids, _MAX_MEDIA)
    return [dict(r) for r in rows]


async def _fetch_faces_for_images(image_ids: list[int]) -> dict[int, list[dict]]:
    """One batched fetch of every face for the given image_ids, grouped by
    image_id. Keeps the O(image * face) inner loop CPU-only (no per-image DB
    round-trip)."""
    if not image_ids:
        return {}
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT f.image_id                AS image_id,
                   f.id                      AS face_id,
                   f.embedding_vec::text     AS emb,
                   COALESCE(f.quality_score, 0.0) AS quality
            FROM facetracker.faces f
            WHERE f.image_id = ANY($1::int[])
              AND f.embedding_vec IS NOT NULL
        """, image_ids)
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(int(r["image_id"]), []).append(dict(r))
    return grouped


async def _fetch_sources_by_media_id(mids: list[str]) -> dict[str, str]:
    """Bulk resolve each collector media_item's source platform. Kept as a
    separate helper because the collector DB is a different pool."""
    if not mids:
        return {}
    collector = get_collector_pool()
    async with collector.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text AS mid, source FROM media_items WHERE id::text = ANY($1::text[])",
            mids,
        )
    return {r["mid"]: r["source"] for r in rows}


def _compute_associates_for_image(
    faces: list[dict],
    primary_emb: "np.ndarray",
    threshold: float,
) -> list[tuple[int, float]]:
    """CPU-bound: parse + L2-normalize each face embedding, cosine against the
    entity's primary face, pick the argmax as A's-own face iff its similarity
    clears `threshold`, return (face_id, quality) for every OTHER face. Empty
    list when A's primary isn't matched (means the entity likely isn't in this
    image — skip it entirely rather than fabricate associates)."""
    if len(faces) < 2:
        return []
    embs = np.asarray([json.loads(f["emb"]) for f in faces], dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs /= norms
    sims = embs @ primary_emb  # (n,)
    if float(sims.max()) < threshold:
        return []
    own_idx = int(sims.argmax())
    return [
        (int(f["face_id"]), float(f["quality"]))
        for i, f in enumerate(faces)
        if i != own_idx
    ]


async def build_face_associations() -> dict:
    """Pipeline phase entry point. Idle-skips on unchanged face corpus. Returns
    {entities_with_primary, media_scanned, associations_added}."""
    global _last_face_count
    analyzer = get_analyzer_pool()

    async with analyzer.acquire() as conn:
        face_count = await conn.fetchval(
            "SELECT count(*) FROM facetracker.faces WHERE embedding_vec IS NOT NULL"
        )
    if face_count == _last_face_count:
        return {"skipped": "no_new_faces", "faces": face_count}

    primary_faces = await _resolve_primary_faces()
    stats = {
        "entities_with_primary": len(primary_faces),
        "media_scanned": 0,
        "associations_added": 0,
    }
    if not primary_faces:
        _last_face_count = face_count
        logger.info("face_associations: %s", stats)
        return stats

    media_rows = await _fetch_owned_multiface_media(list(primary_faces.keys()))
    stats["media_scanned"] = len(media_rows)
    if not media_rows:
        _last_face_count = face_count
        logger.info("face_associations: %s", stats)
        return stats

    faces_by_image = await _fetch_faces_for_images([int(m["image_id"]) for m in media_rows])
    sources_by_mid = await _fetch_sources_by_media_id(
        list({m["mid"] for m in media_rows})
    )

    def _compute_all(rows_, faces_by_img_, primaries_):
        out: list[tuple] = []
        for m in rows_:
            eid = m["entity_id"]
            primary = primaries_.get(eid)
            if primary is None:
                continue
            _pfid, pemb = primary
            faces = faces_by_img_.get(int(m["image_id"]), [])
            others = _compute_associates_for_image(faces, pemb, _ASSOC_THRESHOLD)
            for face_id, quality in others:
                out.append((eid, face_id, m["mid"], quality))
        return out

    computed = await asyncio.to_thread(
        _compute_all, media_rows, faces_by_image, primary_faces
    )

    if computed:
        insert_rows = [
            (eid, face_id, mid, sources_by_mid.get(mid), quality)
            for eid, face_id, mid, quality in computed
        ]
        async with analyzer.acquire() as conn:
            # ON CONFLICT DO NOTHING keeps repeated passes idempotent — the
            # UNIQUE (entity_id, associated_face_id, media_item_id) constraint
            # dedupes across cycles even when the source data hasn't changed.
            result = await conn.executemany("""
                INSERT INTO face_associations
                    (entity_id, associated_face_id, media_item_id, source_platform, quality_score)
                VALUES ($1::uuid, $2, $3, $4, $5)
                ON CONFLICT (entity_id, associated_face_id, media_item_id) DO NOTHING
            """, insert_rows)
            # asyncpg's executemany returns None (no per-row count); the
            # inserted count is bounded above by len(insert_rows). We report
            # the attempted count, which is close enough for phase telemetry
            # and always non-decreasing.
            _ = result
        stats["associations_added"] = len(insert_rows)

    _last_face_count = face_count
    logger.info("face_associations: %s", stats)
    return stats
