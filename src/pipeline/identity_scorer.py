"""
Phase 5A: Identity confidence scorer.

Aggregates all identity_signals rows per (entity_a, entity_b) pair into a
single "same person" probability score, combining heterogeneous weak
signals (bio mentions, content fingerprint similarity, temporal co-posting,
WhatsApp group co-occurrence, shared emails/phones, cross-platform profile
links, shared personal websites, shared Strava route origins) via
probabilistic OR.

Note on target_record_id conventions across signal types:
  - bio_mention: target_record_id is a *platform_id* (raw username/pid) on
    target_platform — NOT an entity_id. Must be resolved via
    entity_platform_links (source, platform_id) -> entity_id.
  - content_similarity, temporal_copost, group_cooccurrence, email_match,
    cross_platform_link, phone_match, shared_website, shared_route_origin:
    target_record_id is the *other entity's UUID* as text directly.

Results are stored in entity_relationships as 'same_person_probability',
following the delete-then-executemany pattern used by
temporal_correlation.correlate_activity() for 'temporal_hour_similarity'.
"""
import json
import logging

from src.db.connection import get_analyzer_pool
from src.pipeline.identity_calibration import pair_feature_vector, get_model, predict_proba

logger = logging.getLogger(__name__)

# P1-1 (identity_system_review_plan.md): these are BETWEEN-entity association
# weights. The resolver's deterministic signals (username_exact, real_name_fuzzy,
# whatsapp_phone, commit_email, profile_photo_sha256) are deliberately NOT listed
# here, and must not be naively added: they are stored INTRA-entity (both
# endpoints resolve to the same entity, and their target_record_id is a
# platform_id, not an entity UUID). The scorer keys pairs on target_record_id and
# casts entity_b to ::uuid, so pulling those rows in would either self-cancel
# (tgt_eid == src_eid, skipped) or crash the insert. Genuinely unifying the two
# identity heads requires computing deterministic overlap BETWEEN candidate entity
# pairs (do entity_a and entity_b share a normalized username / distinctive full
# name / phone / commit email / profile-photo hash?) and emitting those as
# cross-entity signals — i.e. the candidate-generation work staged under P2. Until
# then the resolver stays the authoritative merge head and this stays the advisory
# association head; real_name_fuzzy is demoted at the source in entity_resolver
# (name_is_distinctive + stricter name-only threshold).
# TODO(P1-1/P2): emit cross-entity deterministic signals + add demoted weights here.
_TYPE_WEIGHT = {
    "email_match": 0.60,
    "phone_match": 0.60,
    "bio_mention": 0.40,
    "cross_platform_link": 0.40,
    "content_similarity": 0.30,
    "temporal_copost": 0.30,
    "shared_website": 0.35,
    "shared_route_origin": 0.40,
    "group_cooccurrence": 0.20,
    "media_gps_colocation": 0.40,
    "media_perceptual_match": 0.35,
    "media_face_match": 0.50,
    # P2-7: same physical camera (EXIF body/lens serial) across two entities.
    "media_device_match": 0.55,
    # Cross-entity similar-handle candidate: two separate people share a digit-
    # stripped username (bryanseah vs bryanseah234). Moderate — surfaced in Review.
    "username_similar": 0.45,
    # Axis-3 Change-3: direct ArcFace kNN between two entities' PORTRAIT faces,
    # gated by >= FACE_PAIR_KNN_MIN_MATCHES matches at >= FACE_PAIR_KNN_THRESHOLD
    # cosine. Higher weight than media_face_match (0.50) because portrait gate +
    # min-matches guard make a false positive from siblings/lookalikes far less
    # likely per single row.
    "face_pair_knn": 0.60,
    # Axis-1 MVP: cosine similarity of entity-level timeline_embeddings centroids
    # (multilingual-e5-small over timeline_events.title). Deliberately weak —
    # topical overlap is common among peers in the same industry/subculture, so
    # this is weaker evidence than stylistic content_similarity (0.30) or any
    # deterministic anchor. Emitted by src/pipeline/topical_similarity.py; not a
    # hard-anchor in auto_labeler._HARD_SIGNALS. Append-only (16th entry).
    "topical_similarity": 0.15,
    # Face social graph (2026-07-08): entity B's primary face matched entity A's
    # stored face_association at cosine >= 0.55. ASSOCIATIVE, not
    # identity-direct — a hit means "B is in A's social circle" OR "B is A
    # viewed via a friend's photo", so weight is deliberately below
    # face_pair_knn (0.60, which is direct portrait-vs-portrait) and above
    # topical_similarity (0.15). Append-only; must not reorder.
    "social_face_link": 0.30,
    # NER enrichment: two entities share a RARE ORG/school/location extracted
    # by spaCy from bios + recent timeline titles. Emitted by
    # src/pipeline/shared_life_context.py. Rarity guard (≤5% of entities by
    # default) is what keeps this from being ubiquitous — a shared 'Google' or
    # 'Singapore' produces nothing.
    "shared_life_context": 0.35,
}

_MIN_SCORE = 0.10
_HIGH_CONFIDENCE = 0.70
# 2026-07-08: penalty multiplier for same-platform candidate pairs. Cross-platform
# pairs are unpenalised (multiplier=1.0). Same-platform pairs get score * this,
# which by design dims them in the review queue while keeping them discoverable
# for the rare burner-account case. Tune with env SCORER_SAME_PLATFORM_MULTIPLIER.
import os as _os_scorer
_SAME_PLATFORM_MULTIPLIER = float(_os_scorer.getenv("SCORER_SAME_PLATFORM_MULTIPLIER", "0.3"))
_CROSS_PLATFORM_MULTIPLIER = float(_os_scorer.getenv("SCORER_CROSS_PLATFORM_MULTIPLIER", "1.5"))

def _pair_key(a: str, b: str) -> tuple[str, str]:
    """Normalize pair ordering — lexicographically smaller UUID first."""
    return (a, b) if a < b else (b, a)


async def compute_identity_scores() -> dict:
    analyzer = get_analyzer_pool()

    async with analyzer.acquire() as conn:
        signal_rows = await conn.fetch("""
            SELECT entity_id::text, signal_type, target_platform, target_record_id, confidence
            FROM identity_signals
            WHERE signal_type = ANY($1::text[])
        """, list(_TYPE_WEIGHT.keys()))

        # entity_platform_links lookup for resolving bio_mention's
        # (target_platform, target_record_id) -> entity_id
        link_rows = await conn.fetch(
            "SELECT entity_id::text, source, platform_id FROM entity_platform_links"
        )
        pid_to_entity: dict[tuple[str, str], str] = {
            (l["source"], l["platform_id"]): l["entity_id"] for l in link_rows
        }

        # entity_id -> set of platforms it has links on (for cross_platform calc)
        entity_platforms: dict[str, set[str]] = {}
        for l in link_rows:
            entity_platforms.setdefault(l["entity_id"], set()).add(l["source"])

        # Pairs the user dismissed in the dashboard ("not the same person").
        # Stored normalized (entity_a < entity_b) just like _pair_key, so a
        # dismissed candidate never re-surfaces on the next rebuild.
        # Filter to HUMAN labels only — a future auto_labeler (see
        # src/pipeline/auto_labeler.py) may write label=0 rows with
        # source='auto_negative_*'; those are training data, NOT dismissals,
        # and must not suppress the scorer from surfacing the pair.
        dismissed_rows = await conn.fetch("""
            SELECT entity_a::text AS a, entity_b::text AS b
            FROM identity_labels
            WHERE label = 0 AND (source IS NULL OR source NOT LIKE 'auto\\_%' ESCAPE '\\')
        """)
        dismissed = {(r["a"], r["b"]) for r in dismissed_rows}

    # --- Aggregate contributions per normalized pair ---
    # pair_key -> list of (signal_type, confidence)
    pair_contributions: dict[tuple[str, str], list[tuple[str, float]]] = {}

    skipped_unresolved = 0
    for row in signal_rows:
        src_eid = row["entity_id"]
        sig_type = row["signal_type"]
        confidence = float(row["confidence"] or 0.0)

        if sig_type == "bio_mention":
            tgt_platform = row["target_platform"]
            tgt_pid = row["target_record_id"]
            tgt_eid = pid_to_entity.get((tgt_platform, tgt_pid))
            if not tgt_eid:
                skipped_unresolved += 1
                continue
        else:
            tgt_eid = row["target_record_id"]

        if not tgt_eid or tgt_eid == src_eid:
            continue

        key = _pair_key(src_eid, tgt_eid)
        pair_contributions.setdefault(key, []).append((sig_type, confidence))

    # --- Compute combined score per pair ---
    # Calibrated logistic-regression model when one has been trained
    # (src/pipeline/identity_calibration.py), else fall back to the hand-set
    # noisy-OR. The model handles signal correlation + gives a calibrated
    # probability; the fallback keeps behaviour identical until a model exists.
    model = get_model()
    scoring_method = "calibrated" if model is not None else "noisy_or"
    results: list[dict] = []
    for (a, b), contributions in pair_contributions.items():
        if (a, b) in dismissed:
            continue  # user said these are different people
        breakdown = [{"type": t, "confidence": round(c, 4)} for t, c in contributions]

        if model is not None:
            score = predict_proba(model, pair_feature_vector(contributions))
        else:
            prob_none = 1.0
            for sig_type, confidence in contributions:
                prob_none *= (1 - _TYPE_WEIGHT[sig_type] * confidence)
            score = 1 - prob_none

        platforms_a = entity_platforms.get(a, set())
        platforms_b = entity_platforms.get(b, set())
        # Cross-platform: A and B have at least one DISJOINT platform (i.e.
        # the sets differ, at least one entity has a platform the other
        # doesn't). Same-platform: identical platform sets.
        cross_platform = platforms_a != platforms_b
        same_platform = not cross_platform

        # 2026-07-08: same-platform pairs get a 0.3x penalty. Rationale:
        # if A and B are only on Telegram (say), their platform user_ids
        # differ - so unless it's a burner-account case, they're
        # different people even when usernames or content look similar.
        # Cross-platform pairs remain unpenalised. Tunable via env.
        # score is still emitted (dim + deprioritise on the UI side),
        # NOT excluded - preserves the rare burner discovery path.
        if same_platform:
            score = score * _SAME_PLATFORM_MULTIPLIER
        elif cross_platform:
            score = min(score * _CROSS_PLATFORM_MULTIPLIER, 1.0)

        if score < _MIN_SCORE:
            continue

        results.append({
            "entity_a": a,
            "entity_b": b,
            "score": score,
            "breakdown": breakdown,
            "cross_platform": cross_platform,
            "same_platform": same_platform,
        })

    # --- Persist: delete-and-reinsert ---
    insert_rows: list[tuple] = []
    for r in results:
        sources = {
            "score": round(r["score"], 4),
            "method": scoring_method,
            "same_platform": r["same_platform"],
            "contributing_signals": r["breakdown"],
        }
        insert_rows.append((
            r["entity_a"],
            r["entity_b"],
            "same_person_probability",
            round(r["score"] * 100),
            r["cross_platform"],
            json.dumps(sources, default=str),
        ))

    async with analyzer.acquire() as conn:
        await conn.execute(
            "DELETE FROM entity_relationships WHERE relationship_type = 'same_person_probability'"
        )
        if insert_rows:
            await conn.executemany("""
                INSERT INTO entity_relationships
                    (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb)
            """, insert_rows)

    high_confidence = sum(1 for r in results if r["score"] >= _HIGH_CONFIDENCE)

    stats = {
        "pairs_scored": len(results),
        "high_confidence_pairs": high_confidence,
    }
    if skipped_unresolved:
        logger.debug("Identity scoring: skipped %d bio_mention rows with unresolved target entity", skipped_unresolved)
    logger.info("Identity scoring: %s", stats)
    return stats
