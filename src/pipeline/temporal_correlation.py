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
import os
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from math import sqrt, exp, log10

from scipy.stats import poisson

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

_MIN_EVENTS = 5              # skip entities with too few events
_HOUR_SIM_THRESHOLD = 0.90   # cosine similarity for hour distribution
_COPOST_WINDOW_MIN = 60      # minutes: co-post window
_COPOST_MIN_DAYS = 3         # minimum co-post days to flag
_COPOST_MIN_EVENTS = 3       # minimum co-post coincidence events to consider
_COPOST_CONFIDENCE = 0.70    # base confidence for temporal_copost signal
_COPRESENCE_WINDOW_SEC = 60  # sub-minute/one-minute tight co-presence window
_COPRESENCE_MIN_EVENTS = 3
_COPRESENCE_MIN_DAYS = 2
_COABSENCE_MIN_OVERLAP_DAYS = 21
_COABSENCE_MIN_SHARED_SILENT_DAYS = 7
_COABSENCE_MIN_SHARED_ACTIVE_DAYS = 3
_COABSENCE_MIN_AGREEMENT = 0.82
_COABSENCE_MIN_SILENCE_JACCARD = 0.72
# Significance gate: a pair is only flagged when the observed number of co-post
# coincidences is unlikely under independence. Correlated posting times happen
# by chance (timezone overlap, both active evenings) — a raw count flags those.
# We model expected coincidences as Poisson over the pair's OVERLAPPING active
# window and require p < threshold, Bonferroni-adjusted for the pair count.
_COPOST_PVALUE = float(os.getenv("TEMPORAL_COPOST_PVALUE", "0.01"))
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


def _date_span(start: date, end: date) -> int:
    return (end - start).days + 1


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

    # For pairs with enough data, compute co-post coincidences + significance.
    active_entities = [eid for eid, evts in entity_events.items() if len(evts) >= _MIN_EVENTS]
    active_days_by_entity = {
        eid: sorted({ts.date() for ts in entity_events[eid]})
        for eid in active_entities
    }
    # (a, b, copost_days, k_coincidences, p_value, confidence)
    copost_pairs: list[tuple[str, str, int, int, float, float]] = []
    copresence_pairs: list[tuple[str, str, int, int, float, float]] = []
    coabsence_pairs: list[tuple[str, str, int, int, int, float, float, float]] = []

    window = timedelta(minutes=_COPOST_WINDOW_MIN)
    # Bonferroni: we evaluate up to C(n,2) pairs; divide the per-test alpha by
    # the number of pairs so the family-wise false-positive rate stays at
    # _COPOST_PVALUE rather than ballooning with the pair count.
    n_pairs = len(active_entities) * (len(active_entities) - 1) // 2
    pval_threshold = _COPOST_PVALUE / max(1, n_pairs)

    for i, eid_a in enumerate(active_entities):
        for eid_b in active_entities[i + 1:]:
            ts_a = entity_events[eid_a]
            ts_b = entity_events[eid_b]
            # Restrict to the window where BOTH are active so one entity's long
            # history can't inflate the rate estimate (and so chance is modeled
            # over the period the two could actually have co-posted).
            lo = max(ts_a[0], ts_b[0])
            hi = min(ts_a[-1], ts_b[-1])
            if hi <= lo:
                continue
            span_min = (hi - lo).total_seconds() / 60.0
            a_ov = [t for t in ts_a if lo <= t <= hi]
            b_ov = [t for t in ts_b if lo <= t <= hi]
            if len(a_ov) < _MIN_EVENTS or len(b_ov) < _MIN_EVENTS or span_min <= 0:
                continue

            # Observed: A events with >=1 B event within +/- window (each A
            # event counted once). Two-pointer over sorted timestamps.
            k = 0
            copost_days: set[str] = set()
            j = 0
            for ts in a_ov:
                while j < len(b_ov) and b_ov[j] < ts - window:
                    j += 1
                if j < len(b_ov) and b_ov[j] <= ts + window:
                    k += 1
                    copost_days.add(ts.strftime("%Y-%m-%d"))
            if k < _COPOST_MIN_EVENTS or len(copost_days) < _COPOST_MIN_DAYS:
                continue

            # Expected under independence: prob a given A event has >=1 B event
            # within +/- window ~ 1 - exp(-rate_b * 2*window) (Poisson approx of
            # at-least-one B arrival in the 2*window interval). lambda = N_a*p_hit.
            rate_b = len(b_ov) / span_min            # B events per minute
            p_hit = 1.0 - exp(-rate_b * 2.0 * _COPOST_WINDOW_MIN)
            lam = len(a_ov) * p_hit
            pval = float(poisson.sf(k - 1, lam)) if lam > 0 else 0.0
            if pval > pval_threshold:
                continue  # not significant — chance-level co-posting

            # Confidence scales with significance (more standard deviations from
            # chance -> higher), capped.
            conf = min(0.90, _COPOST_CONFIDENCE + min(0.20, -log10(max(pval, 1e-30)) * 0.02))
            copost_pairs.append((eid_a, eid_b, len(copost_days), k, pval, round(conf, 3)))

            tight_window = timedelta(seconds=_COPRESENCE_WINDOW_SEC)
            k_tight = 0
            copresence_days: set[str] = set()
            j_tight = 0
            for ts in a_ov:
                while j_tight < len(b_ov) and b_ov[j_tight] < ts - tight_window:
                    j_tight += 1
                if j_tight < len(b_ov) and b_ov[j_tight] <= ts + tight_window:
                    k_tight += 1
                    copresence_days.add(ts.strftime("%Y-%m-%d"))
            if k_tight >= _COPRESENCE_MIN_EVENTS and len(copresence_days) >= _COPRESENCE_MIN_DAYS:
                rate_b_tight = len(b_ov) / (span_min * 60.0)
                p_hit_tight = 1.0 - exp(-rate_b_tight * 2.0 * _COPRESENCE_WINDOW_SEC)
                lam_tight = len(a_ov) * p_hit_tight
                pval_tight = float(poisson.sf(k_tight - 1, lam_tight)) if lam_tight > 0 else 0.0
                if pval_tight <= pval_threshold:
                    conf_tight = min(0.95, 0.75 + min(0.20, -log10(max(pval_tight, 1e-30)) * 0.025))
                    copresence_pairs.append((eid_a, eid_b, len(copresence_days), k_tight, pval_tight, round(conf_tight, 3)))

            days_a = active_days_by_entity.get(eid_a) or []
            days_b = active_days_by_entity.get(eid_b) or []
            if not days_a or not days_b:
                continue
            overlap_start = max(days_a[0], days_b[0])
            overlap_end = min(days_a[-1], days_b[-1])
            overlap_days = _date_span(overlap_start, overlap_end)
            if overlap_days < _COABSENCE_MIN_OVERLAP_DAYS:
                continue
            set_a = {day for day in days_a if overlap_start <= day <= overlap_end}
            set_b = {day for day in days_b if overlap_start <= day <= overlap_end}
            active_union = len(set_a | set_b)
            active_intersection = len(set_a & set_b)
            silent_intersection = overlap_days - active_union
            if active_intersection < _COABSENCE_MIN_SHARED_ACTIVE_DAYS:
                continue
            if silent_intersection < _COABSENCE_MIN_SHARED_SILENT_DAYS:
                continue
            silent_union = overlap_days - active_intersection
            if silent_union <= 0:
                continue
            agreement = (active_intersection + silent_intersection) / overlap_days
            silence_jaccard = silent_intersection / silent_union
            if agreement < _COABSENCE_MIN_AGREEMENT or silence_jaccard < _COABSENCE_MIN_SILENCE_JACCARD:
                continue
            conf_abs = min(
                0.92,
                0.68
                + min(0.12, max(0.0, agreement - _COABSENCE_MIN_AGREEMENT) * 0.7)
                + min(0.12, max(0.0, silence_jaccard - _COABSENCE_MIN_SILENCE_JACCARD) * 0.6)
                + min(0.05, active_intersection * 0.01),
            )
            coabsence_pairs.append((
                eid_a,
                eid_b,
                overlap_days,
                active_intersection,
                silent_intersection,
                round(silence_jaccard, 3),
                round(agreement, 3),
                round(conf_abs, 3),
            ))

    stats = {
        "entities_analyzed": len(hour_dists),
        "tier1_similar_pairs": len(tier1_pairs),
        "tier2_copost_pairs": len(copost_pairs),
        "copresence_pairs": len(copresence_pairs),
        "coabsence_pairs": len(coabsence_pairs),
    }

    # --- Persist results ---
    async with analyzer.acquire() as conn:
        # Tier 1: store in entity_relationships
        await conn.execute(
            "DELETE FROM entity_relationships WHERE relationship_type = 'temporal_hour_similarity'"
        )
        await conn.execute(
            "DELETE FROM entity_relationships WHERE relationship_type = 'co_presence'"
        )
        await conn.execute(
            "DELETE FROM entity_relationships WHERE relationship_type = 'co_absence'"
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

        for eid_a, eid_b, days, k, pval, conf in copresence_pairs:
            a, b = (eid_a, eid_b) if eid_a < eid_b else (eid_b, eid_a)
            weight = max(1, round(conf * 100) + min(40, k * 3 + days * 2))
            try:
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'co_presence', $3, true, $4::jsonb)
                    ON CONFLICT DO NOTHING
                """, a, b, weight, json.dumps({
                    "window_seconds": _COPRESENCE_WINDOW_SEC,
                    "coincident_events": k,
                    "copresence_days": days,
                    "p_value": float(f"{pval:.3e}"),
                    "confidence": conf,
                    "why": "Repeated activity within a sub-minute window suggests tight co-presence.",
                }))
            except Exception:
                logger.debug("co_presence insert failed for %s/%s", a, b, exc_info=True)

        for eid_a, eid_b, overlap_days, shared_active_days, shared_silent_days, silence_jaccard, agreement, conf in coabsence_pairs:
            a, b = (eid_a, eid_b) if eid_a < eid_b else (eid_b, eid_a)
            weight = max(1, round(conf * 100) + min(35, shared_silent_days * 2 + shared_active_days * 3))
            try:
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'co_absence', $3, true, $4::jsonb)
                    ON CONFLICT DO NOTHING
                """, a, b, weight, json.dumps({
                    "overlap_days": overlap_days,
                    "shared_active_days": shared_active_days,
                    "shared_silent_days": shared_silent_days,
                    "silence_jaccard": silence_jaccard,
                    "agreement": agreement,
                    "confidence": conf,
                    "why": "Their active and quiet windows move together over time, including matched silence periods.",
                }))
            except Exception:
                logger.debug("co_absence insert failed for %s/%s", a, b, exc_info=True)

        # Tier 2: store as identity signals (temporal_copost) — preserve across runs
        await conn.execute(
            "DELETE FROM identity_signals WHERE signal_type = 'temporal_copost'"
        )
        if copost_pairs:
            signal_rows = [
                (eid_a, "temporal_copost", "timeline", None, None, None,
                 "timeline", eid_b, f"copost_days:{days},events:{k},p:{pval:.2e}", conf)
                for eid_a, eid_b, days, k, pval, conf in copost_pairs
            ]
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, signal_rows)

    logger.info("Temporal correlation: %s", stats)
    return stats
