"""Face social graph — social_face_link signal emitter (2026-07-08).

Fires whenever entity B's primary face matches any face stored as another
entity A's associate at cosine >= 0.55. Two possible readings:
  1. B is in A's social circle (the friend in the group photo).
  2. B is A viewed via a friend's photo (a same-person hint — the "friend of
     a friend is you" case, particularly common when a duplicate identity B
     was created because A never bridged their own profile-photo face).

The scorer treats social_face_link as an *associative* signal (weight 0.30 in
identity_scorer._TYPE_WEIGHT — below the deterministic-ish face_pair_knn at
0.60), so a single hit can't drive a merge on its own; it needs corroborating
evidence.

STORAGE: one identity_signals row per (owner=A, target=B) directed pair. Delete-
then-insert per pass keeps the derived signals atomic and self-cleaning
(identical to the rebuild pattern in face_pair_signals /
media_analysis_tier1._build_face_match_signals).

ENV:
  SOCIAL_FACE_LINK_ENABLED     (default '1') — set to '0' to skip the phase
  SOCIAL_FACE_LINK_THRESHOLD   (default 0.55) — cosine gate
  SOCIAL_FACE_LINK_MAX_PAIRS   (default 500)  — highest-confidence cap per pass
"""
import asyncio
import json
import logging
import os

import numpy as np

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("SOCIAL_FACE_LINK_ENABLED", "1") == "1"


async def emit_social_face_link_signals() -> dict:
    """Compute cross-entity social_face_link signals from primary_face vs
    face_associations. Idempotent (delete-then-insert). Returns
    {entities_scanned, signals_emitted, max_cosine}."""
    if not _enabled():
        return {"skipped": "disabled",
                "SOCIAL_FACE_LINK_ENABLED": os.getenv("SOCIAL_FACE_LINK_ENABLED", "1")}

    threshold = float(os.getenv("SOCIAL_FACE_LINK_THRESHOLD", "0.55"))
    max_pairs = int(os.getenv("SOCIAL_FACE_LINK_MAX_PAIRS", "500"))

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        # Entity B's primary face — one row per entity that has one set.
        b_rows = await conn.fetch("""
            SELECT e.id::text          AS entity_id,
                   f.id                AS face_id,
                   f.embedding_vec::text AS emb
            FROM entities e
            JOIN facetracker.faces f ON f.id = e.primary_face_id
            WHERE e.primary_face_id IS NOT NULL
              AND f.embedding_vec IS NOT NULL
        """)
        # Every stored associate face with its owning entity + originating media.
        # The owning entity here is entity A (the poster of the group photo);
        # we score each A's associate against every B's primary face below.
        assoc_rows = await conn.fetch("""
            SELECT fa.entity_id::text     AS owner_id,
                   fa.associated_face_id  AS face_id,
                   fa.media_item_id       AS mid,
                   f.embedding_vec::text  AS emb
            FROM face_associations fa
            JOIN facetracker.faces f ON f.id = fa.associated_face_id
            WHERE f.embedding_vec IS NOT NULL
        """)

    stats = {"entities_scanned": len(b_rows), "signals_emitted": 0, "max_cosine": 0.0}
    if not b_rows or not assoc_rows:
        # Still run the DELETE so a stale signal set from a prior pass is not
        # left lingering when the source data has emptied out.
        async with analyzer.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM identity_signals WHERE signal_type = 'social_face_link'"
                )
                await conn.execute(
                    "DELETE FROM entity_relationships WHERE relationship_type = 'mutual_social_face'"
                )
        logger.info("social_face_link signals: %s (no candidates)", stats)
        return stats

    def _score(b_rows_, assoc_rows_):
        """CPU-bound: build the (len(B) x len(A)) cosine matrix in one BLAS
        call and pick the max cosine per (owner, target_B) pair >= threshold.
        L2-normalize both sides so IndexFlatIP == cosine. Runs off the loop."""
        b_ids = [r["entity_id"] for r in b_rows_]
        Bp = np.asarray([json.loads(r["emb"]) for r in b_rows_], dtype=np.float32)
        bn = np.linalg.norm(Bp, axis=1, keepdims=True); bn[bn == 0] = 1.0
        Bp /= bn

        assoc_owners = [r["owner_id"] for r in assoc_rows_]
        assoc_face_ids = [int(r["face_id"]) for r in assoc_rows_]
        assoc_mids = [r["mid"] for r in assoc_rows_]
        Am = np.asarray([json.loads(r["emb"]) for r in assoc_rows_], dtype=np.float32)
        an = np.linalg.norm(Am, axis=1, keepdims=True); an[an == 0] = 1.0
        Am /= an

        # sims[b, a] = cosine(B_primary[b], associate[a])
        sims = Bp @ Am.T  # (n_b, n_a)

        # Best (owner, target_B) pair — max cosine wins, drop pairs where
        # owner == target_B (the entity's own primary matching its own
        # associate is not a social link, it's the trivial self-hit case).
        pair_best: dict[tuple[str, str], tuple[float, int, str]] = {}
        for bi in range(sims.shape[0]):
            b_eid = b_ids[bi]
            # Vectorized filter: candidate associate indices at/above threshold.
            over = np.where(sims[bi] >= threshold)[0]
            for ai in over:
                owner = assoc_owners[int(ai)]
                if owner == b_eid:
                    continue
                s = float(sims[bi, int(ai)])
                key = (owner, b_eid)
                cur = pair_best.get(key)
                if cur is None or s > cur[0]:
                    pair_best[key] = (s, assoc_face_ids[int(ai)], assoc_mids[int(ai)])
        max_seen = float(sims.max()) if sims.size else 0.0
        return pair_best, max_seen

    pair_best, max_seen = await asyncio.to_thread(_score, b_rows, assoc_rows)

    # Cap at the top-N most-confident pairs so a runaway result set can't blow
    # up identity_signals in one pass. Descending cosine ordering makes the
    # cut deterministic across restarts.
    items = sorted(pair_best.items(), key=lambda kv: kv[1][0], reverse=True)[:max_pairs]

    new_signals: list[tuple] = []
    for (owner, target_b), (cos, face_id, mid) in items:
        metadata = {
            "cosine": round(cos, 4),
            "associated_face_id": face_id,
            "media_item_id": mid,
            "target_entity_id": target_b,
        }
        # confidence rounded to 2dp + capped at 0.99 mirrors the pattern in
        # media_analysis_tier1._build_face_match_signals — leaves headroom
        # above other rows and avoids exact-1.0 confidences in the scorer.
        new_signals.append((
            owner,                       # $1 entity_id (A, the associate's owner)
            "social_face_link",          # $2 signal_type
            "facetracker",               # $3 source_platform
            "face_associations",         # $4 source_table
            None,                        # $5 source_column
            None,                        # $6 source_record_id
            "face_association",          # $7 target_platform
            target_b,                    # $8 target_record_id (B's entity_id text)
            f"face_assoc:{face_id}",     # $9 value
            round(min(cos, 0.99), 4),    # $10 confidence
            json.dumps(metadata),        # $11 metadata JSONB
        ))

    async with analyzer.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM identity_signals WHERE signal_type = 'social_face_link'"
            )
            if new_signals:
                await conn.executemany("""
                    INSERT INTO identity_signals
                        (entity_id, signal_type, source_platform, source_table, source_column,
                         source_record_id, target_platform, target_record_id, value, confidence, metadata)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                """, new_signals)

            # Mutual social face logic (T1.3)
            mutual_pairs = set()
            for (owner, target_b), (cos, _, _) in items:
                if (target_b, owner) in pair_best:
                    # To avoid duplicate (A->B and B->A), just sort them
                    a, b = (owner, target_b) if owner < target_b else (target_b, owner)
                    if (a, b) not in mutual_pairs:
                        mutual_pairs.add((a, b))
            
            await conn.execute(
                "DELETE FROM entity_relationships WHERE relationship_type = 'mutual_social_face'"
            )
            if mutual_pairs:
                rel_rows = []
                for a, b in mutual_pairs:
                    rel_rows.append((
                        a, b, "mutual_social_face", 90, True,
                        json.dumps({"method": "social_face_link_bilateral_check"})
                    ))
                await conn.executemany("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb)
                    ON CONFLICT (entity_a_id, entity_b_id, relationship_type)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        cross_platform = EXCLUDED.cross_platform,
                        sources = EXCLUDED.sources,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = NOW()
                """, rel_rows)

    stats["signals_emitted"] = len(new_signals)
    stats["mutual_social_faces"] = len(mutual_pairs)
    stats["max_cosine"] = round(max_seen, 4)
    logger.info("social_face_link signals: %s", stats)
    return stats
