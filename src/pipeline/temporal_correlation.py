"""
Phase 4C: Temporal activity correlation.

Two-tier analysis:
  Tier 1 (fast): Cosine similarity of posting_hour_dist between entities.
                 Stored in entity_relationships as 'temporal_hour_similarity'.
  Tier 2 (strong): Count of days where two entities BOTH post within a
                   short window — potential same-person or coordination signal.
                   Emitted as identity_signals (temporal_copost).

Filters out timeline_events with bogus 1970 timestamps.
"""
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from math import sqrt

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

_MIN_EVENTS = 5              # skip entities with too few events
_HOUR_SIM_THRESHOLD = 0.90   # cosine similarity for hour distribution
_COPOST_WINDOW_MIN = 60      # minutes: co-post window
_COPOST_MIN_DAYS = 3         # minimum co-post days to flag
_COPOST_CONFIDENCE = 0.70    # confidence for temporal_copost signal
_EPOCH_FLOOR = datetime(2010, 1, 1, tzinfo=timezone.utc)


def _decode_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sqrt(sum(x * x for x in a))
    mag_b = sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def correlate_activity() -> dict:
    analyzer = get_analyzer_pool()

    # --- Tier 1: posting hour distribution similarity ---
    async with analyzer.acquire() as conn:
        profiles = await conn.fetch("""
            SELECT entity_id::text, posting_hour_dist, total_events
            FROM behavioral_profiles
            WHERE posting_hour_dist IS NOT NULL AND total_events >= $1
        """, _MIN_EVENTS)

    hour_dists: dict[str, list[float]] = {}
    for p in profiles:
        dist = p["posting_hour_dist"]
        # behavioral_profiler stores as JSONB dict {"0": 5, "14": 3, ...}
        if isinstance(dist, str):
            try:
                dist = json.loads(dist)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(dist, dict):
            hour_dists[p["entity_id"]] = [float(dist.get(str(h), 0)) for h in range(24)]
        elif isinstance(dist, list) and len(dist) == 24:
            hour_dists[p["entity_id"]] = [float(x) for x in dist]

    entity_ids = list(hour_dists)
    tier1_pairs: list[tuple[str, str, float]] = []  # (a, b, similarity)
    for i, eid_a in enumerate(entity_ids):
        for eid_b in entity_ids[i + 1:]:
            sim = _cosine(hour_dists[eid_a], hour_dists[eid_b])
            if sim >= _HOUR_SIM_THRESHOLD:
                tier1_pairs.append((eid_a, eid_b, sim))

    # --- Tier 2: co-post day/window analysis ---
    async with analyzer.acquire() as conn:
        events = await conn.fetch("""
            SELECT entity_id::text, occurred_at, source
            FROM timeline_events
            WHERE entity_id IS NOT NULL
              AND occurred_at > $1
            ORDER BY entity_id, occurred_at
        """, _EPOCH_FLOOR)

    # Build per-entity sorted event timestamps
    entity_events: dict[str, list[datetime]] = defaultdict(list)
    for e in events:
        entity_events[e["entity_id"]].append(e["occurred_at"])

    # For pairs with enough data, compute co-post days
    active_entities = [eid for eid, evts in entity_events.items() if len(evts) >= _MIN_EVENTS]
    copost_pairs: list[tuple[str, str, int, float]] = []  # (a, b, copost_days, confidence)

    window = timedelta(minutes=_COPOST_WINDOW_MIN)
    for i, eid_a in enumerate(active_entities):
        for eid_b in active_entities[i + 1:]:
            ts_a = entity_events[eid_a]
            ts_b = entity_events[eid_b]
            # Merge and scan for windows where both post close together
            copost_days: set[str] = set()
            j = 0
            for ts in ts_a:
                # Find all ts_b events within window of ts
                while j < len(ts_b) and ts_b[j] < ts - window:
                    j += 1
                k = j
                while k < len(ts_b) and ts_b[k] <= ts + window:
                    copost_days.add(ts.strftime("%Y-%m-%d"))
                    k += 1
            if len(copost_days) >= _COPOST_MIN_DAYS:
                # Scale confidence by number of co-post days (cap at 20)
                conf = min(_COPOST_CONFIDENCE + (len(copost_days) - _COPOST_MIN_DAYS) * 0.02, 0.85)
                copost_pairs.append((eid_a, eid_b, len(copost_days), round(conf, 3)))

    stats = {
        "entities_analyzed": len(hour_dists),
        "tier1_similar_pairs": len(tier1_pairs),
        "tier2_copost_pairs": len(copost_pairs),
    }

    # --- Persist results ---
    async with analyzer.acquire() as conn:
        # Tier 1: store in entity_relationships
        await conn.execute(
            "DELETE FROM entity_relationships WHERE relationship_type = 'temporal_hour_similarity'"
        )
        for eid_a, eid_b, sim in tier1_pairs:
            a, b = (eid_a, eid_b) if eid_a < eid_b else (eid_b, eid_a)
            try:
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'temporal_hour_similarity', $3, true, $4::jsonb)
                    ON CONFLICT DO NOTHING
                """, a, b, round(sim * 100), json.dumps({"similarity": round(sim, 4)}))
            except Exception:
                logger.debug("Tier1 insert failed for %s/%s", a, b, exc_info=True)

        # Tier 2: store as identity signals (temporal_copost) — preserve across runs
        await conn.execute(
            "DELETE FROM identity_signals WHERE signal_type = 'temporal_copost'"
        )
        if copost_pairs:
            signal_rows = [
                (eid_a, "temporal_copost", "timeline", None, None, None,
                 "timeline", eid_b, f"copost_days:{days}", conf)
                for eid_a, eid_b, days, conf in copost_pairs
            ]
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, signal_rows)

    logger.info("Temporal correlation: %s", stats)
    return stats
