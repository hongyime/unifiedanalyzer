"""
Phase 4C: Temporal activity correlation.

Two-tier analysis:
  Tier 1 (fast): Rarity-weighted similarity of posting_hour_dist between
                 entities, GATED on real tight co-occurrence evidence.
                 Stored in entity_relationships as 'temporal_hour_similarity'.
  Tier 2 (context): Count of days where two entities BOTH post within a short
                    window. This is coordination/context only, not same-person
                    evidence, and is stored in entity_relationships.

Filters out timeline_events with bogus 1970 timestamps.

Q4 precision hardening (2026-07-16)
-----------------------------------
The old Tier 1 was a plain cosine of the 24-bucket HOURLY posting histogram
with a 0.90 threshold. That produced ~1,284 low-precision edges: any two
accounts that both happen to be active in a popular hour (e.g. 14:00 or the
evening) scored ~1.0 and looked "posting in sync". The worst offenders were
single-hour / single-day burst accounts (e.g. an entity with all 52 events in
hour 14 on one day) — their histogram is a spike, so cosine==1.0 with EVERY
other account that ever posts at 14:00, and high-frequency accounts that span
all 24 hours, which coincide with everyone.

Four independent tightenings, each env-tunable:
  1. EXCLUDE HIGH-FREQUENCY / DEGENERATE ACCOUNTS. An entity with more than
     TEMPORAL_HUB_MAX_EVENTS events (it coincides with everyone), or that is
     active on fewer than TEMPORAL_MIN_ACTIVE_DAYS distinct days, or that
     spreads over fewer than TEMPORAL_MIN_DISTINCT_HOURS distinct hours, is not
     a stable temporal fingerprint and is skipped for Tier 1.
  2. RARITY (statistical-improbability) WEIGHTING. Replace plain cosine with an
     inverse-frequency (IDF) weighted cosine: each hour is weighted by how rare
     posting in that hour is across the WHOLE population, so agreement in a rare
     3am hour scores far higher than agreement in the busy 14:00 / evening
     hours everybody shares.
  3. REQUIRE A MINIMUM COUNT OF TIGHT CO-OCCURRENCES. A Tier 1 edge is only
     emitted when the pair ALSO has >= TEMPORAL_MIN_TIGHT_COOCCUR real events
     inside a SHORT window (TEMPORAL_TIGHT_WINDOW_SEC seconds) on
     >= TEMPORAL_MIN_TIGHT_DAYS distinct days, and that tight-coincidence count
     is itself statistically improbable under independence (Poisson test, same
     machinery as co_presence). Hourly-bucket agreement alone is never enough.
  4. RAISED, RARITY-SCALED THRESHOLD. The weighted-similarity floor is
     TEMPORAL_HOUR_SIM_THRESHOLD and the emitted weight now reflects the
     rarity-weighted score, not a flat 100.
"""
import asyncio
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
# Q4: Tier 1 hour-similarity precision gates (all env-tunable). The default
# threshold is raised from 0.90 to 0.93 and applied to an IDF-weighted cosine,
# not the raw one.
_HOUR_SIM_THRESHOLD = float(os.getenv("TEMPORAL_HOUR_SIM_THRESHOLD", "0.93"))
# Accounts above this many events coincide with everyone (they post in every
# hour) — they inflate hour-similarity into a hub. Excluded from Tier 1.
_HUB_MAX_EVENTS = int(os.getenv("TEMPORAL_HUB_MAX_EVENTS", "2000"))
# A single-day burst or a one/two-hour spike is not a stable temporal
# fingerprint — its histogram is a spike that matches anyone posting then.
_MIN_ACTIVE_DAYS = int(os.getenv("TEMPORAL_MIN_ACTIVE_DAYS", "3"))
_MIN_DISTINCT_HOURS = int(os.getenv("TEMPORAL_MIN_DISTINCT_HOURS", "3"))
# A Tier 1 edge additionally requires this many statistically-improbable tight
# co-occurrences (within TEMPORAL_TIGHT_WINDOW_SEC) on this many distinct days.
_TIGHT_WINDOW_SEC = int(os.getenv("TEMPORAL_TIGHT_WINDOW_SEC", "120"))
_MIN_TIGHT_COOCCUR = int(os.getenv("TEMPORAL_MIN_TIGHT_COOCCUR", "3"))
_MIN_TIGHT_DAYS = int(os.getenv("TEMPORAL_MIN_TIGHT_DAYS", "2"))
_COPOST_WINDOW_MIN = 60      # minutes: co-post window
_COPOST_MIN_DAYS = 3         # minimum co-post days to flag
_COPOST_MIN_EVENTS = 3       # minimum co-post coincidence events to consider
_COPOST_CONFIDENCE = 0.70    # base confidence for temporal_copost relationship
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


def _hour_idf_weights(hour_dists: dict[str, list[float]]) -> list[float]:
    """Inverse-document-frequency weight per hour over the whole population.

    An hour that only a few entities ever post in is rare/improbable, so
    agreement there is strong evidence; the busy 14:00 / evening hours that
    almost everyone shares get a low weight. weight[h] = log(N / (1 + df[h]))
    where df[h] is the number of entities that post at all in hour h. Floored at
    a small positive value so a universal hour still contributes a little."""
    n = max(1, len(hour_dists))
    df = [0] * 24
    for dist in hour_dists.values():
        for h in range(24):
            if dist[h] > 0:
                df[h] += 1
    return [max(0.05, log10(n / (1.0 + df[h]))) for h in range(24)]


def _weighted_cosine(a: list[float], b: list[float], w: list[float]) -> float:
    """Cosine of the two hour histograms after scaling each hour by its rarity
    weight w[h]. Rare-hour agreement dominates; common-hour agreement barely
    moves the score."""
    wa = [a[h] * w[h] for h in range(24)]
    wb = [b[h] * w[h] for h in range(24)]
    return _cosine(wa, wb)


def _tight_cooccurrence(
    a_ts: list[datetime], b_ts: list[datetime], window_sec: int
) -> tuple[int, int, float]:
    """Count A events with >=1 B event inside +/- window_sec, the number of
    distinct days those fall on, and the Poisson p-value of that count under
    independence (B modelled as a Poisson process over the pair's overlapping
    active span). Two-pointer over sorted timestamps. Returns (k, days, pval)."""
    if len(a_ts) < _MIN_EVENTS or len(b_ts) < _MIN_EVENTS:
        return 0, 0, 1.0
    lo = max(a_ts[0], b_ts[0])
    hi = min(a_ts[-1], b_ts[-1])
    if hi <= lo:
        return 0, 0, 1.0
    a_ov = [t for t in a_ts if lo <= t <= hi]
    b_ov = [t for t in b_ts if lo <= t <= hi]
    if len(a_ov) < _MIN_EVENTS or len(b_ov) < _MIN_EVENTS:
        return 0, 0, 1.0
    span_sec = (hi - lo).total_seconds()
    if span_sec <= 0:
        return 0, 0, 1.0
    win = timedelta(seconds=window_sec)
    k = 0
    days: set[str] = set()
    j = 0
    for ts in a_ov:
        while j < len(b_ov) and b_ov[j] < ts - win:
            j += 1
        if j < len(b_ov) and b_ov[j] <= ts + win:
            k += 1
            days.add(ts.strftime("%Y-%m-%d"))
    if k == 0:
        return 0, 0, 1.0
    rate_b = len(b_ov) / span_sec                      # B events per second
    p_hit = 1.0 - exp(-rate_b * 2.0 * window_sec)      # P(>=1 B in +/- window)
    lam = len(a_ov) * p_hit
    pval = float(poisson.sf(k - 1, lam)) if lam > 0 else 1.0
    return k, len(days), pval


def _date_span(start: date, end: date) -> int:
    return (end - start).days + 1


def _compute_temporal_candidates(
    hour_dists: dict[str, list[float]],
    entity_events: dict[str, list[datetime]],
) -> tuple[
    dict[str, int],
    list[tuple[str, str, float]],
    list[tuple[str, str, int, int, float, float]],
    list[tuple[str, str, int, int, float, float]],
    list[tuple[str, str, int, int, int, float, float, float]],
]:
    # --- Tier 1: rarity-weighted hour similarity, GATED on tight co-occurrence ---
    # Q4: exclude hub / degenerate accounts before pairing. An entity qualifies
    # only if it is not a high-frequency hub, is active on enough distinct days,
    # and spreads over enough distinct hours — otherwise its histogram is a
    # spike that spuriously matches anyone active in the same popular hour.
    tier1_eligible: list[str] = []
    for eid, dist in hour_dists.items():
        total = sum(dist)
        distinct_hours = sum(1 for v in dist if v > 0)
        active_days = len({ts.date() for ts in entity_events.get(eid, ())})
        if total > _HUB_MAX_EVENTS:
            continue                       # hub: coincides with everyone
        if active_days < _MIN_ACTIVE_DAYS:
            continue                       # single-/few-day burst: not stable
        if distinct_hours < _MIN_DISTINCT_HOURS:
            continue                       # one/two-hour spike: not a fingerprint
        tier1_eligible.append(eid)

    idf = _hour_idf_weights({e: hour_dists[e] for e in tier1_eligible})
    tier1_pairs: list[tuple[str, str, float]] = []  # (a, b, weighted_similarity)
    for i, eid_a in enumerate(tier1_eligible):
        for eid_b in tier1_eligible[i + 1:]:
            sim = _weighted_cosine(hour_dists[eid_a], hour_dists[eid_b], idf)
            if sim < _HOUR_SIM_THRESHOLD:
                continue
            # Gate on real tight co-occurrence: hourly-bucket agreement is not
            # enough. Require >= _MIN_TIGHT_COOCCUR statistically-improbable
            # coincidences within a short window on >= _MIN_TIGHT_DAYS days.
            k, tdays, tpval = _tight_cooccurrence(
                entity_events[eid_a], entity_events[eid_b], _TIGHT_WINDOW_SEC
            )
            if k < _MIN_TIGHT_COOCCUR or tdays < _MIN_TIGHT_DAYS:
                continue
            if tpval > _COPOST_PVALUE:
                continue                   # tight coincidences are chance-level
            tier1_pairs.append((eid_a, eid_b, sim))

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
        "tier1_eligible_entities": len(tier1_eligible),
        "tier1_similar_pairs": len(tier1_pairs),
        "tier2_copost_pairs": len(copost_pairs),
        "copresence_pairs": len(copresence_pairs),
        "coabsence_pairs": len(coabsence_pairs),
    }
    return stats, tier1_pairs, copost_pairs, copresence_pairs, coabsence_pairs


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

    # Fetch per-entity event timestamps up front — Tier 1 now needs them to
    # gate hour-similarity on real tight co-occurrence evidence, and Tier 2
    # reuses the same buffer.
    async with analyzer.acquire() as conn:
        events = await conn.fetch("""
            SELECT entity_id::text, occurred_at, source
            FROM timeline_events
            WHERE entity_id IS NOT NULL
              AND occurred_at > $1
            ORDER BY entity_id, occurred_at
        """, _EPOCH_FLOOR)

    # Build per-entity sorted event timestamps.
    entity_events: dict[str, list[datetime]] = defaultdict(list)
    for e in events:
        entity_events[e["entity_id"]].append(e["occurred_at"])

    (
        stats,
        tier1_pairs,
        copost_pairs,
        copresence_pairs,
        coabsence_pairs,
    ) = await asyncio.to_thread(
        _compute_temporal_candidates,
        hour_dists,
        dict(entity_events),
    )

    # --- Persist results ---
    async with analyzer.acquire() as conn:
        async with conn.transaction():
            # Tier 1: store in entity_relationships
            await conn.execute(
                "DELETE FROM entity_relationships WHERE relationship_type = 'temporal_hour_similarity'"
            )
            await conn.execute(
                "DELETE FROM entity_relationships WHERE relationship_type = 'temporal_copost'"
            )
            await conn.execute(
                "DELETE FROM entity_relationships WHERE relationship_type = 'co_presence'"
            )
            await conn.execute(
                "DELETE FROM entity_relationships WHERE relationship_type = 'co_absence'"
            )
            for eid_a, eid_b, sim in tier1_pairs:
                a, b = (eid_a, eid_b) if eid_a < eid_b else (eid_b, eid_a)
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'temporal_hour_similarity', $3, true, $4::jsonb)
                    ON CONFLICT (entity_a_id, entity_b_id, relationship_type)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        cross_platform = EXCLUDED.cross_platform,
                        sources = EXCLUDED.sources,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = NOW()
                """, a, b, round(sim * 100), json.dumps({
                    "weighted_similarity": round(sim, 4),
                    "why": "Rarity-weighted posting-hour agreement, confirmed by "
                           "repeated statistically-improbable tight co-occurrences.",
                }))

            for eid_a, eid_b, days, k, pval, conf in copost_pairs:
                a, b = (eid_a, eid_b) if eid_a < eid_b else (eid_b, eid_a)
                weight = max(1, round(conf * 100) + min(30, k * 2 + days))
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'temporal_copost', $3, true, $4::jsonb)
                    ON CONFLICT (entity_a_id, entity_b_id, relationship_type)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        cross_platform = EXCLUDED.cross_platform,
                        sources = EXCLUDED.sources,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = NOW()
                """, a, b, weight, json.dumps({
                    "window_minutes": _COPOST_WINDOW_MIN,
                    "copost_days": days,
                    "coincident_events": k,
                    "p_value": float(f"{pval:.3e}"),
                    "confidence": conf,
                    "context_only": True,
                    "not_identity_evidence": True,
                    "why": "Repeated close posting windows. Context only; this does not mean the accounts are the same person.",
                }))

            for eid_a, eid_b, days, k, pval, conf in copresence_pairs:
                a, b = (eid_a, eid_b) if eid_a < eid_b else (eid_b, eid_a)
                weight = max(1, round(conf * 100) + min(40, k * 3 + days * 2))
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'co_presence', $3, true, $4::jsonb)
                    ON CONFLICT (entity_a_id, entity_b_id, relationship_type)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        cross_platform = EXCLUDED.cross_platform,
                        sources = EXCLUDED.sources,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = NOW()
                """, a, b, weight, json.dumps({
                    "window_seconds": _COPRESENCE_WINDOW_SEC,
                    "coincident_events": k,
                    "copresence_days": days,
                    "p_value": float(f"{pval:.3e}"),
                    "confidence": conf,
                    "why": "Repeated activity within a sub-minute window suggests tight co-presence.",
                }))

            for eid_a, eid_b, overlap_days, shared_active_days, shared_silent_days, silence_jaccard, agreement, conf in coabsence_pairs:
                a, b = (eid_a, eid_b) if eid_a < eid_b else (eid_b, eid_a)
                weight = max(1, round(conf * 100) + min(35, shared_silent_days * 2 + shared_active_days * 3))
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'co_absence', $3, true, $4::jsonb)
                    ON CONFLICT (entity_a_id, entity_b_id, relationship_type)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        cross_platform = EXCLUDED.cross_platform,
                        sources = EXCLUDED.sources,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = NOW()
                """, a, b, weight, json.dumps({
                    "overlap_days": overlap_days,
                    "shared_active_days": shared_active_days,
                    "shared_silent_days": shared_silent_days,
                    "silence_jaccard": silence_jaccard,
                    "agreement": agreement,
                    "confidence": conf,
                    "why": "Their active and quiet windows move together over time, including matched silence periods.",
                }))

            # Historical cleanup: timing overlap is relationship/context only,
            # not identity evidence. Remove old signal rows and do not reinsert.
            await conn.execute(
                "DELETE FROM identity_signals WHERE signal_type = 'temporal_copost'"
            )

    logger.info("Temporal correlation: %s", stats)
    return stats
