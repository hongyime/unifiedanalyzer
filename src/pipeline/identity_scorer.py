"""
Phase 5A: Identity confidence scorer.

Aggregates all identity_signals rows per (entity_a, entity_b) pair into a
single "same person" probability score, combining heterogeneous weak
signals (bio mentions, content fingerprint similarity, temporal co-posting,
WhatsApp group co-occurrence, shared emails/phones, cross-platform profile
links, shared personal websites) via probabilistic OR.

Note on target_record_id conventions across signal types:
  - bio_mention: target_record_id is a *platform_id* (raw username/pid) on
    target_platform — NOT an entity_id. Must be resolved via
    entity_platform_links (source, platform_id) -> entity_id.
  - content_similarity, temporal_copost, group_cooccurrence, email_match,
    cross_platform_link, phone_match, shared_website: target_record_id is
    the *other entity's UUID* as text directly.

Results are stored in entity_relationships as 'same_person_probability',
following the delete-then-executemany pattern used by
temporal_correlation.correlate_activity() for 'temporal_hour_similarity'.
"""
import json
import logging

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

_TYPE_WEIGHT = {
    "email_match": 0.60,
    "phone_match": 0.60,
    "bio_mention": 0.40,
    "cross_platform_link": 0.40,
    "content_similarity": 0.30,
    "temporal_copost": 0.30,
    "shared_website": 0.35,
    "group_cooccurrence": 0.20,
}

_MIN_SCORE = 0.10
_HIGH_CONFIDENCE = 0.70


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

    # --- Compute combined score per pair via probabilistic OR ---
    results: list[dict] = []
    for (a, b), contributions in pair_contributions.items():
        prob_none = 1.0
        breakdown = []
        for sig_type, confidence in contributions:
            contribution = _TYPE_WEIGHT[sig_type] * confidence
            prob_none *= (1 - contribution)
            breakdown.append({"type": sig_type, "confidence": round(confidence, 4)})

        score = 1 - prob_none
        if score < _MIN_SCORE:
            continue

        platforms_a = entity_platforms.get(a, set())
        platforms_b = entity_platforms.get(b, set())
        cross_platform = platforms_a != platforms_b

        results.append({
            "entity_a": a,
            "entity_b": b,
            "score": score,
            "breakdown": breakdown,
            "cross_platform": cross_platform,
        })

    # --- Persist: delete-and-reinsert ---
    insert_rows: list[tuple] = []
    for r in results:
        sources = {
            "score": round(r["score"], 4),
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
