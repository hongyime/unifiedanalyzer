"""Auto-seed identity_labels from ground-truth signal confluence.

Rationale: identity_calibration's logistic-regression model needs a labeled
training set. Human labels come in slowly through dashboard merge/dismiss
actions (see src/api/routes/entity_actions.py). This module auto-labels pairs
where multiple independent identity signal domains agree, producing training
data without human effort.

Positive predicate (source = 'auto_positive_v1'):
    (>=1 hard-anchor signal AND >=2 total distinct signal types)
    OR (>=3 total distinct signal types)

Hard-anchor signals (see _HARD_SIGNALS) are the deterministic types where a
single fire alone is close to ground truth. Requiring corroboration guards
against LR feature-leak — the trained model must not shortcut to
"signal-X-fires -> positive". Additional defense: identity_calibration
_rows_to_xy() LOSO-expands auto-labeled positives so each contributing signal
is zeroed in turn, forcing the LR to learn multi-signal rules.

Negative predicate: DEFERRED. Human dismissals (dashboard_dismiss, label=0) are
authoritative negatives; synthetic negatives from zero-signal pairs would need
careful class balancing and aren't strictly needed today. Add here if imbalance
becomes a training issue.

Precedence: this module uses INSERT ... ON CONFLICT DO NOTHING. Human labels
use DO UPDATE (see identity_calibration.record_label), so a human decision
ALWAYS beats an auto-label. Auto-labels never overwrite anything.

Gating: AUTO_LABEL_ENABLED env flag (default '0'/off). Even when enabled, this
is a no-op on datasets without confluence — as of 2026-07-05 the live dataset
produces 0 auto-labels (all 254 candidate pairs fire on only 1 signal type),
but the code activates automatically as data grows.

Cross-references (identity_scorer.py):
- The scorer's dismissed-pair guard at :102-105 must filter to human labels
  only, else future auto-negatives would suppress legitimate scoring. Update
  applied in the same slice as this module.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

# Signals where a SINGLE fire is close to same-person ground truth (still
# require corroboration to avoid feature-leak in LR training).
_HARD_SIGNALS: frozenset[str] = frozenset({
    "username_exact", "whatsapp_phone", "commit_email", "profile_photo_sha256",
    "phone_match", "email_match",
    "cross_platform_link", "shared_website",
    "media_face_match", "media_perceptual_match",
    "media_gps_colocation", "media_device_match",
    # Axis-3 Change-3: face_pair_knn is deterministic-ish once the threshold +
    # min-matches gates pass (portrait faces + >=N cosine matches at >=0.55).
    "face_pair_knn",
})

# All live cross-entity scoring signal types (mirrors identity_scorer._TYPE_WEIGHT).
# Historical calibration slots can exist in FEATURE_ORDER without being active
# same-person evidence.
_SCORING_SIGNALS: frozenset[str] = frozenset({
    "username_exact", "real_name_fuzzy", "whatsapp_phone", "commit_email",
    "profile_photo_sha256",
    "email_match", "phone_match", "bio_mention", "cross_platform_link",
    "content_similarity", "shared_website",
    "shared_route_origin", "group_cooccurrence", "media_gps_colocation",
    "media_perceptual_match", "media_face_match", "media_device_match",
    "username_similar",
    # Axis-3 Change-3: cross-entity ArcFace kNN signal.
    "face_pair_knn",
    # Axis-1 MVP: entity-centroid cosine over timeline_embeddings. Weak;
    # deliberately NOT added to _HARD_SIGNALS — topical overlap is not
    # deterministic (peers/co-workers commonly share topics).
    "topical_similarity",
    # NER enrichment: shared rare ORG/school/location. Also weak — a shared
    # small employer is suggestive but not deterministic (colleagues exist).
    # Deliberately NOT in _HARD_SIGNALS.
    "shared_life_context",
    # Face social graph (2026-07-08): associative — B's primary face matched
    # A's face_association. Deliberately NOT a hard-anchor: a hit means "B is
    # in A's social circle" OR "B is A viewed via a friend's photo", the
    # former is common and does not imply same-person.
    "social_face_link",
})

_AUTO_POSITIVE_SOURCE = "auto_positive_v1"


def _is_enabled() -> bool:
    return os.getenv("AUTO_LABEL_ENABLED", "0") == "1"


_QUALIFY_SQL = """
WITH pairs AS (
    SELECT LEAST(entity_id::text, target_record_id) AS a,
           GREATEST(entity_id::text, target_record_id) AS b,
           signal_type
    FROM identity_signals
    WHERE signal_type = ANY($1::text[])
      AND target_record_id ~ '^[0-9a-f-]{36}$'
      AND entity_id::text <> target_record_id
), agg AS (
    SELECT a, b,
           count(DISTINCT signal_type) AS n_types,
           count(DISTINCT signal_type) FILTER (WHERE signal_type = ANY($2::text[])) AS n_hard
    FROM pairs GROUP BY 1, 2
)
SELECT a, b, n_types, n_hard FROM agg
WHERE (n_hard >= 1 AND n_types >= 2) OR n_types >= 3
"""


async def _qualifying_pairs(conn) -> list:
    return await conn.fetch(
        _QUALIFY_SQL, list(_SCORING_SIGNALS), list(_HARD_SIGNALS)
    )


async def seed_ground_truth_labels() -> dict:
    """Insert auto_positive_v1 rows for pairs meeting the confluence predicate.

    Runs as a pipeline phase (see incremental_runner._secondary_phases). Cheap:
    one aggregate SELECT + up to a few INSERTs; safe to run every incremental
    cycle. Returns counts for logging / per-phase status."""
    from src.db.connection import get_analyzer_pool
    from src.pipeline.identity_calibration import snapshot_pair_features

    if not _is_enabled():
        return {"skipped": "disabled",
                "AUTO_LABEL_ENABLED": os.getenv("AUTO_LABEL_ENABLED", "0")}

    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        candidates = await _qualifying_pairs(conn)

        added = 0
        for row in candidates:
            a, b = row["a"], row["b"]
            feats = await snapshot_pair_features(conn, a, b)
            if not feats:
                continue
            status = await conn.execute("""
                INSERT INTO identity_labels (entity_a, entity_b, features, label, source)
                VALUES ($1::uuid, $2::uuid, $3::jsonb, 1, $4)
                ON CONFLICT (entity_a, entity_b) DO NOTHING
            """, a, b, json.dumps(feats), _AUTO_POSITIVE_SOURCE)
            # asyncpg execute returns "INSERT 0 N" where N is the row count.
            if status.endswith(" 1"):
                added += 1

    stats = {"candidates": len(candidates),
             "auto_positive_added": added,
             "source": _AUTO_POSITIVE_SOURCE}
    logger.info("Auto-labeler: %s", stats)
    return stats


def _main():
    """CLI: `python -m src.pipeline.auto_labeler [--dry-run]`.
    Default action: honour AUTO_LABEL_ENABLED and (if enabled) seed labels.
    --dry-run: report qualifying pair count without inserting."""
    import asyncio
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    dry_run = "--dry-run" in sys.argv[1:]

    async def _run():
        from src.db.connection import init_pools, close_pools, get_analyzer_pool
        await init_pools(apply_schema_ddl=False)
        try:
            if dry_run:
                pool = get_analyzer_pool()
                async with pool.acquire() as conn:
                    rows = await _qualifying_pairs(conn)
                print(f"dry-run: {len(rows)} pair(s) would qualify as {_AUTO_POSITIVE_SOURCE}")
                for r in rows[:20]:
                    print(f"  {r['a']} <-> {r['b']}  n_types={r['n_types']}  n_hard={r['n_hard']}")
            else:
                if not _is_enabled():
                    print("AUTO_LABEL_ENABLED=0 - set to 1 to enable, or use --dry-run to preview.")
                    return
                print(json.dumps(await seed_ground_truth_labels(), indent=2))
        finally:
            await close_pools()

    asyncio.run(_run())


if __name__ == "__main__":
    _main()
