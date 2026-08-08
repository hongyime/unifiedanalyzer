import os
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from src.db.connection import get_analyzer_pool
from src.merge_candidates import merge_candidate_min_weight
from src.pipeline.face_bridge_audit import audit_face_bridge_collisions

logger = logging.getLogger(__name__)

# Phase 5B epoch floor — same pattern as temporal_correlation.py, skips bogus 1970 timestamps
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


def _env_bool(key: str, default: bool = True) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _env_float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


async def run_alerts() -> dict:
    stats = {
        "silence_gap": 0,
        "new_activity": 0,
        "profile_change": 0,
        "new_identity_link": 0,
        "coordinated_posting": 0,
        "location_mismatch": 0,
        "emotional_spike": 0,
        "face_link_drift": 0,
        "location_evidence_spike": 0,
    }

    if _env_bool("SILENCE_GAP_DYNAMIC", True):
        stats["silence_gap"] = await _detect_silence_gaps()

    if _env_bool("NEW_ACTIVITY_AFTER_SILENCE_ENABLED", True):
        stats["new_activity"] = await _detect_new_activity_after_silence()

    if _env_bool("PROFILE_CHANGE_ALERT_ENABLED", True):
        stats["profile_change"] = await _detect_profile_changes()

    if _env_bool("NEW_IDENTITY_LINK_ALERT_ENABLED", True):
        stats["new_identity_link"] = await _detect_new_identity_links()

    if _env_bool("COORDINATED_POSTING_ALERT_ENABLED", False):
        stats["coordinated_posting"] = await _detect_coordinated_posting()

    if _env_bool("LOCATION_MISMATCH_ALERT_ENABLED", True):
        stats["location_mismatch"] = await _detect_location_mismatches()

    if _env_bool("EMOTIONAL_SPIKE_ALERT_ENABLED", True):
        stats["emotional_spike"] = await _detect_emotional_spikes()

    if _env_bool("FACE_LINK_DRIFT_ALERT_ENABLED", True):
        stats["face_link_drift"] = await _detect_face_link_drift()

    if _env_bool("LOCATION_EVIDENCE_SPIKE_ALERT_ENABLED", True):
        stats["location_evidence_spike"] = await _detect_location_evidence_spikes()

    logger.info("Alert engine complete: %s", stats)
    return stats


async def _detect_silence_gaps() -> int:
    pool = get_analyzer_pool()
    dynamic = _env_bool("SILENCE_GAP_DYNAMIC", True)
    min_history = _env_int("SILENCE_GAP_MIN_HISTORY_DAYS", 14)
    fixed_days = _env_int("SILENCE_GAP_FIXED_DAYS", 7)
    multiplier = _env_float("SILENCE_GAP_DYNAMIC_MULTIPLIER", 2.5)
    min_days = _env_int("SILENCE_GAP_MIN_DAYS", 3)
    max_days = _env_int("SILENCE_GAP_MAX_DAYS", 30)

    # P1-3 (identity_system_review_plan.md): SILENCE_GAP must not fire when the
    # silence is explained by a COLLECTION gap rather than the subject going quiet.
    # On a scraper, if a source stalls, every entity on it appears to go silent at
    # once (this is why SILENCE_GAP was ~93% of all alerts). Suppress the alert for
    # an entity whose most-recent-event source has itself produced no events for
    # ANYONE within source_stale_days — i.e. the source (not the person) stopped.
    suppress_on_source_stall = _env_bool("SILENCE_GAP_SUPPRESS_ON_SOURCE_STALL", True)
    source_stale_days = _env_int("SILENCE_GAP_SOURCE_STALE_DAYS", 2)

    now = datetime.now(timezone.utc)
    count = 0

    async with pool.acquire() as conn:
        # Per-source global last event: is the SOURCE still collecting at all?
        source_health_rows = await conn.fetch("""
            SELECT source, MAX(occurred_at) AS last_event
            FROM timeline_events
            GROUP BY source
        """)
        source_last_event = {r["source"]: r["last_event"] for r in source_health_rows}

        entity_rows = await conn.fetch("""
            WITH event_stats AS (
                SELECT
                    entity_id,
                    MAX(occurred_at) AS last_event,
                    MIN(occurred_at) AS first_event,
                    COUNT(*) AS event_count
                FROM timeline_events
                WHERE entity_id IS NOT NULL
                GROUP BY entity_id
            ),
            last_sources AS (
                SELECT DISTINCT ON (entity_id)
                    entity_id,
                    source AS last_source
                FROM timeline_events
                WHERE entity_id IS NOT NULL
                ORDER BY entity_id, occurred_at DESC
            )
            SELECT
                e.id,
                e.canonical_name,
                e.silence_threshold_days,
                s.last_event,
                s.first_event,
                s.event_count,
                ls.last_source
            FROM entities e
            JOIN event_stats s ON s.entity_id = e.id
            LEFT JOIN last_sources ls ON ls.entity_id = e.id
            WHERE e.tier = 'primary'
        """)

        for entity in entity_rows:
            eid = entity["id"]
            last_event = entity["last_event"]
            first_event = entity["first_event"]
            event_count = entity["event_count"]
            history_days = (now - first_event).days if first_event else 0

            custom_threshold = entity["silence_threshold_days"]
            if custom_threshold and custom_threshold > 0:
                threshold_days = custom_threshold
            elif dynamic and history_days >= min_history and event_count >= 2:
                span = (last_event - first_event).total_seconds() / 86400.0
                avg_interval = span / (event_count - 1) if event_count > 1 else span
                threshold_days = avg_interval * multiplier
                threshold_days = max(min_days, min(threshold_days, max_days))
            else:
                threshold_days = fixed_days

            gap_days = (now - last_event).total_seconds() / 86400.0
            if gap_days >= threshold_days:
                # P1-3: is this entity's silence just a stalled source? If the
                # source of its latest event has produced nothing for anyone in
                # source_stale_days, the gap is a collection outage — skip.
                if suppress_on_source_stall:
                    last_src = entity["last_source"]
                    src_last = source_last_event.get(last_src)
                    if src_last is not None and (now - src_last).days >= source_stale_days:
                        continue

                existing = await conn.fetchval("""
                    SELECT id FROM alerts
                    WHERE entity_id = $1
                      AND alert_type = 'SILENCE_GAP'
                      AND detected_at > $2
                """, eid, now - timedelta(days=threshold_days))

                if not existing:
                    await conn.execute("""
                        INSERT INTO alerts (entity_id, alert_type, severity, title, detail)
                        VALUES ($1, 'SILENCE_GAP', 'warning', $2, $3)
                    """, eid,
                        f"{entity['canonical_name'] or 'Unknown'} silent for {gap_days:.0f} days",
                        json.dumps({
                            "gap_days": round(gap_days, 1),
                            "threshold_days": round(threshold_days, 1),
                            "last_event": last_event.isoformat(),
                            "mode": "dynamic" if dynamic and history_days >= min_history else "fixed",
                        }))
                    count += 1

    return count


async def _detect_emotional_spikes() -> int:
    pool = get_analyzer_pool()
    min_baseline = _env_int("EMOTIONAL_SPIKE_MIN_BASELINE_EVENTS", 20)
    min_current = _env_int("EMOTIONAL_SPIKE_MIN_CURRENT_EVENTS", 5)
    z_threshold = _env_float("EMOTIONAL_SPIKE_Z_THRESHOLD", 2.5)
    confidence_floor = _env_float("EMOTIONAL_SPIKE_CONFIDENCE_FLOOR", 0.55)
    count = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            WITH baseline AS (
                SELECT entity_id,
                       count(*)::int AS baseline_count,
                       avg(vader_compound) AS baseline_mean,
                       stddev_pop(vader_compound) AS baseline_stddev
                FROM timeline_text_features
                WHERE entity_id IS NOT NULL
                  AND vader_compound IS NOT NULL
                  AND occurred_at >= NOW() - INTERVAL '60 days'
                  AND occurred_at < NOW() - INTERVAL '24 hours'
                  AND COALESCE(sentiment_confidence, 0) >= $3
                GROUP BY entity_id
            ),
            current AS (
                SELECT entity_id,
                       count(*)::int AS event_count,
                       avg(vader_compound) AS current_mean,
                       avg(sentiment_confidence) AS avg_confidence,
                       array_agg(event_id::text ORDER BY occurred_at DESC)[:10] AS event_ids
                FROM timeline_text_features
                WHERE entity_id IS NOT NULL
                  AND vader_compound IS NOT NULL
                  AND occurred_at >= NOW() - INTERVAL '24 hours'
                  AND COALESCE(sentiment_confidence, 0) >= $3
                GROUP BY entity_id
            )
            SELECT c.entity_id::text, e.canonical_name,
                   b.baseline_count, c.event_count, b.baseline_mean,
                   COALESCE(NULLIF(b.baseline_stddev, 0), 0.05) AS baseline_stddev,
                   c.current_mean, c.avg_confidence, c.event_ids
            FROM current c
            JOIN baseline b ON b.entity_id = c.entity_id
            JOIN entities e ON e.id = c.entity_id
            WHERE b.baseline_count >= $1
              AND c.event_count >= $2
              AND abs((c.current_mean - b.baseline_mean) / COALESCE(NULLIF(b.baseline_stddev, 0), 0.05)) >= $4
        """, min_baseline, min_current, confidence_floor, z_threshold)
        for row in rows:
            z_score = (float(row["current_mean"]) - float(row["baseline_mean"])) / float(row["baseline_stddev"])
            key = f"{row['entity_id']}:{datetime.now(timezone.utc).date().isoformat()}:{'positive' if z_score > 0 else 'negative'}"
            existing = await conn.fetchval("""
                SELECT id FROM alerts
                WHERE alert_type = 'EMOTIONAL_SPIKE'
                  AND detail->>'dedupe_key' = $1
            """, key)
            if existing:
                continue
            await conn.execute("""
                INSERT INTO alerts (entity_id, alert_type, severity, title, detail)
                VALUES ($1::uuid, 'EMOTIONAL_SPIKE', $2, $3, $4)
            """, row["entity_id"],
                "warning" if abs(z_score) >= z_threshold + 1 else "info",
                f"Emotional tone spike for {row['canonical_name'] or row['entity_id']}",
                json.dumps({
                    "dedupe_key": key,
                    "context_only": True,
                    "baseline_window_days": 60,
                    "event_window_hours": 24,
                    "baseline_count": row["baseline_count"],
                    "event_count": row["event_count"],
                    "baseline_mean": float(row["baseline_mean"]),
                    "baseline_stddev": float(row["baseline_stddev"]),
                    "current_mean": float(row["current_mean"]),
                    "z_score": round(z_score, 3),
                    "confidence": float(row["avg_confidence"]),
                    "event_ids": list(row["event_ids"] or []),
                }, default=str))
            count += 1
    return count


async def _detect_face_link_drift() -> int:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        report = await audit_face_bridge_collisions(conn, sample_limit=5)
        if not report.get("available") or report.get("ok") is not False:
            return 0
        key = json.dumps({
            "face": report.get("face_entity_collisions"),
            "cluster": report.get("cluster_entity_collisions"),
        }, sort_keys=True)
        existing = await conn.fetchval("""
            SELECT id FROM alerts
            WHERE alert_type = 'FACE_LINK_DRIFT'
              AND detected_at > NOW() - INTERVAL '24 hours'
              AND detail->>'drift_key' = $1
        """, key)
        if existing:
            return 0
        await conn.execute("""
            INSERT INTO alerts (alert_type, severity, title, detail)
            VALUES ('FACE_LINK_DRIFT', 'warning', $1, $2)
        """, "Face link audit found collision drift", json.dumps({
            "drift_key": key,
            "face_entity_collisions": report.get("face_entity_collisions"),
            "cluster_entity_collisions": report.get("cluster_entity_collisions"),
            "contested_cluster_count": report.get("contested_cluster_count"),
            "samples": report.get("samples", {}),
        }, default=str))
    return 1


async def _detect_location_evidence_spikes() -> int:
    pool = get_analyzer_pool()
    min_events = _env_int("LOCATION_EVIDENCE_SPIKE_MIN_EVENTS", 8)
    min_confidence = _env_float("LOCATION_EVIDENCE_SPIKE_MIN_CONFIDENCE", 0.55)
    count = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT le.entity_id::text, e.canonical_name, le.source,
                   count(*)::int AS event_count,
                   avg(le.confidence) AS avg_confidence,
                   array_agg(le.evidence_key ORDER BY le.occurred_at DESC)[:10] AS evidence_keys
            FROM location_evidence le
            JOIN entities e ON e.id = le.entity_id
            WHERE COALESCE(le.status, 'active') = 'active'
              AND COALESCE(le.confidence, 0) >= $2
              AND le.occurred_at >= NOW() - INTERVAL '24 hours'
              AND COALESCE(le.evidence_type, '') NOT IN ('caption_derived', 'venue_geocode')
            GROUP BY le.entity_id, e.canonical_name, le.source
            HAVING count(*) >= $1
        """, min_events, min_confidence)
        for row in rows:
            key = f"{row['entity_id']}:{row['source']}:{datetime.now(timezone.utc).date().isoformat()}"
            existing = await conn.fetchval("""
                SELECT id FROM alerts
                WHERE alert_type = 'LOCATION_EVIDENCE_SPIKE'
                  AND detail->>'dedupe_key' = $1
            """, key)
            if existing:
                continue
            await conn.execute("""
                INSERT INTO alerts (entity_id, alert_type, severity, source, title, detail)
                VALUES ($1::uuid, 'LOCATION_EVIDENCE_SPIKE', 'info', $2, $3, $4)
            """, row["entity_id"], row["source"],
                f"Location evidence spike for {row['canonical_name'] or row['entity_id']}",
                json.dumps({
                    "dedupe_key": key,
                    "event_window_hours": 24,
                    "event_count": row["event_count"],
                    "avg_confidence": float(row["avg_confidence"] or 0),
                    "evidence_keys": list(row["evidence_keys"] or []),
                    "public_place_suppressed_types": ["caption_derived", "venue_geocode"],
                }, default=str))
            count += 1
    return count


async def _detect_new_activity_after_silence() -> int:
    pool = get_analyzer_pool()
    event_types = [t.strip() for t in os.getenv(
        "NEW_ACTIVITY_EVENT_TYPES",
        "CONTENT_PUBLISHED,MESSAGE_SENT,PHYSICAL_ACTIVITY,CODE_COMMIT,VIDEO_PUBLISHED"
    ).split(",")]

    count = 0
    now = datetime.now(timezone.utc)
    lookback = timedelta(hours=2)

    async with pool.acquire() as conn:
        recent_events = await conn.fetch("""
            SELECT DISTINCT entity_id
            FROM timeline_events
            WHERE occurred_at > $1
              AND event_type = ANY($2)
              AND entity_id IS NOT NULL
        """, now - lookback, event_types)

        for row in recent_events:
            eid = row["entity_id"]

            prev = await conn.fetchrow("""
                SELECT occurred_at FROM timeline_events
                WHERE entity_id = $1
                  AND event_type = ANY($2)
                  AND occurred_at < $3
                ORDER BY occurred_at DESC
                LIMIT 1
            """, eid, event_types, now - lookback)

            if not prev:
                continue

            gap_days = (now - lookback - prev["occurred_at"]).total_seconds() / 86400.0

            entity = await conn.fetchrow(
                "SELECT canonical_name FROM entities WHERE id = $1", eid
            )

            bp = await conn.fetchrow("""
                SELECT avg_post_interval_days FROM behavioral_profiles WHERE entity_id = $1
            """, eid)

            threshold = 7.0
            if bp and bp["avg_post_interval_days"]:
                threshold = bp["avg_post_interval_days"] * _env_float(
                    "SILENCE_GAP_DYNAMIC_MULTIPLIER", 2.5
                )
                threshold = max(
                    _env_int("SILENCE_GAP_MIN_DAYS", 3),
                    min(threshold, _env_int("SILENCE_GAP_MAX_DAYS", 30))
                )

            if gap_days >= threshold:
                existing = await conn.fetchval("""
                    SELECT id FROM alerts
                    WHERE entity_id = $1
                      AND alert_type = 'NEW_ACTIVITY_AFTER_SILENCE'
                      AND detected_at > $2
                """, eid, now - timedelta(days=1))

                if not existing:
                    name = entity["canonical_name"] if entity else "Unknown"
                    await conn.execute("""
                        INSERT INTO alerts (entity_id, alert_type, severity, title, detail)
                        VALUES ($1, 'NEW_ACTIVITY_AFTER_SILENCE', 'info', $2, $3)
                    """, eid,
                        f"{name} active again after {gap_days:.0f} days of silence",
                        json.dumps({
                            "gap_days": round(gap_days, 1),
                            "threshold_days": round(threshold, 1),
                        }))
                    count += 1

    return count


async def _detect_profile_changes() -> int:
    pool = get_analyzer_pool()
    count = 0

    async with pool.acquire() as conn:
        links = await conn.fetch("""
            SELECT epl.id, epl.entity_id, epl.source, epl.platform_id,
                   epl.platform_username, epl.platform_name,
                   e.canonical_name
            FROM entity_platform_links epl
            JOIN entities e ON epl.entity_id = e.id
        """)

        for link in links:
            prev_alert = await conn.fetchrow("""
                SELECT detail FROM alerts
                WHERE entity_id = $1
                  AND alert_type = 'PROFILE_CHANGE'
                  AND source = $2
                ORDER BY detected_at DESC LIMIT 1
            """, link["entity_id"], link["source"])

            if prev_alert and prev_alert["detail"]:
                try:
                    prev = json.loads(prev_alert["detail"]) if isinstance(
                        prev_alert["detail"], str
                    ) else prev_alert["detail"]
                except (json.JSONDecodeError, TypeError):
                    prev = {}

                changes = []
                if prev.get("username") and prev["username"] != link["platform_username"]:
                    changes.append(f"username: {prev['username']} -> {link['platform_username']}")
                if prev.get("name") and prev["name"] != link["platform_name"]:
                    changes.append(f"name: {prev['name']} -> {link['platform_name']}")

                if changes:
                    name = link["canonical_name"] or "Unknown"
                    await conn.execute("""
                        INSERT INTO alerts (entity_id, alert_type, severity, source, title, detail)
                        VALUES ($1, 'PROFILE_CHANGE', 'info', $2, $3, $4)
                    """, link["entity_id"], link["source"],
                        f"{name} changed profile on {link['source']}",
                        json.dumps({
                            "changes": changes,
                            "username": link["platform_username"],
                            "name": link["platform_name"],
                        }))
                    count += 1

    return count


async def _detect_new_identity_links() -> int:
    """
    Phase 5B: NEW_IDENTITY_LINK.

    identity_scorer.compute_identity_scores() (runs at the END of the pipeline,
    AFTER run_alerts()) stores high-confidence "same person" pairs in
    entity_relationships as relationship_type='same_person_probability'.

    Because of pipeline ordering, run_alerts() sees results from the PREVIOUS
    pipeline run — a 1-cycle lag is accepted by design (see Phase 5B spec).
    """
    pool = get_analyzer_pool()
    threshold_weight = merge_candidate_min_weight()
    count = 0

    async with pool.acquire() as conn:
        pairs = await conn.fetch("""
            SELECT er.entity_a_id::text AS a_id, er.entity_b_id::text AS b_id,
                   er.weight, er.sources,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships er
            JOIN entities ea ON ea.id = er.entity_a_id
            JOIN entities eb ON eb.id = er.entity_b_id
            WHERE er.relationship_type = 'same_person_probability'
              AND COALESCE(
                    CASE WHEN jsonb_typeof(er.sources->'score') = 'number'
                         THEN (er.sources->>'score')::float8 * 100
                    END,
                    er.weight
                  ) >= $1
        """, threshold_weight)

        for pair in pairs:
            a_id, b_id = pair["a_id"], pair["b_id"]

            existing = await conn.fetchval("""
                SELECT id FROM alerts
                WHERE alert_type = 'NEW_IDENTITY_LINK'
                  AND detail->>'entity_a_id' = $1
                  AND detail->>'entity_b_id' = $2
            """, a_id, b_id)

            if existing:
                continue

            sources = _decode_meta(pair["sources"])
            score = sources.get("score")
            score_pct = round(score * 100) if isinstance(score, (int, float)) else pair["weight"]

            name_a = pair["name_a"] or "Unknown"
            name_b = pair["name_b"] or "Unknown"

            await conn.execute("""
                INSERT INTO alerts (entity_id, alert_type, severity, title, detail)
                VALUES ($1::uuid, 'NEW_IDENTITY_LINK', 'warning', $2, $3)
            """, a_id,
                f"Possible same-person link: {name_a} <-> {name_b} ({score_pct}%)",
                json.dumps({
                    "entity_a_id": a_id,
                    "entity_b_id": b_id,
                    "name_a": name_a,
                    "name_b": name_b,
                    "score": score,
                    "weight": pair["weight"],
                    "contributing_signals": sources.get("contributing_signals", []),
                }))
            count += 1

    return count


async def _detect_coordinated_posting() -> int:
    """
    Phase 5B: COORDINATED_POSTING.

    For pairs of DIFFERENT entities posting to DIFFERENT platforms within a
    10-minute window of each other, count cross-platform close-in-time
    co-occurrences over the last 30 days. Pairs with >= 3 such occurrences
    get an alert (deduped via alerts.detail jsonb, same as NEW_IDENTITY_LINK).

    Uses a sorted-event-scan (per temporal_correlation.py's Tier 2 approach)
    rather than an O(n^2) cartesian join.
    """
    pool = get_analyzer_pool()
    min_occurrences = _env_int("COORDINATED_POSTING_MIN_OCCURRENCES", 3)
    window = timedelta(minutes=_env_int("COORDINATED_POSTING_WINDOW_MINUTES", 10))
    lookback_days = _env_int("COORDINATED_POSTING_LOOKBACK_DAYS", 30)
    max_events = _env_int("COORDINATED_POSTING_MAX_EVENTS", 20000)
    max_entities = _env_int("COORDINATED_POSTING_MAX_ENTITIES", 500)
    count = 0

    now = datetime.now(timezone.utc)
    since = max(_EPOCH_FLOOR, now - timedelta(days=lookback_days))

    async with pool.acquire() as conn:
        events = await conn.fetch("""
            WITH recent AS (
                SELECT entity_id::text, occurred_at, source
                FROM timeline_events
                WHERE entity_id IS NOT NULL
                  AND occurred_at > $1
                ORDER BY occurred_at DESC
                LIMIT $2
            )
            SELECT entity_id::text, occurred_at, source
            FROM recent
            ORDER BY occurred_at
        """, since, max_events)

        # entity_id -> sorted list of (occurred_at, source)
        entity_events: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        for e in events:
            entity_events[e["entity_id"]].append((e["occurred_at"], e["source"]))

        if len(events) >= max_events:
            logger.warning(
                "COORDINATED_POSTING capped at %d recent events; lower lookback or raise cap if needed",
                max_events,
            )

        entity_ids = sorted(
            entity_events,
            key=lambda eid: len(entity_events[eid]),
            reverse=True,
        )[:max_entities]
        if len(entity_events) > max_entities:
            logger.warning(
                "COORDINATED_POSTING capped at %d busiest entities out of %d",
                max_entities,
                len(entity_events),
            )

        # pair_key (a < b) -> count of cross-platform co-occurrences within window
        pair_counts: dict[tuple[str, str], int] = defaultdict(int)

        for i, eid_a in enumerate(entity_ids):
            ts_a = entity_events[eid_a]
            for eid_b in entity_ids[i + 1:]:
                ts_b = entity_events[eid_b]
                j = 0
                for occ_a, src_a in ts_a:
                    while j < len(ts_b) and ts_b[j][0] < occ_a - window:
                        j += 1
                    k = j
                    while k < len(ts_b) and ts_b[k][0] <= occ_a + window:
                        occ_b, src_b = ts_b[k]
                        if src_a != src_b:
                            key = (eid_a, eid_b) if eid_a < eid_b else (eid_b, eid_a)
                            pair_counts[key] += 1
                        k += 1

        candidates = {k: v for k, v in pair_counts.items() if v >= min_occurrences}
        if not candidates:
            return 0

        # Fetch names for candidate entities
        all_ids = {eid for pair in candidates for eid in pair}
        name_rows = await conn.fetch("""
            SELECT id::text, canonical_name FROM entities WHERE id = ANY($1::uuid[])
        """, list(all_ids))
        names = {r["id"]: (r["canonical_name"] or "Unknown") for r in name_rows}

        for (a_id, b_id), occurrences in candidates.items():
            existing = await conn.fetchval("""
                SELECT id FROM alerts
                WHERE alert_type = 'COORDINATED_POSTING'
                  AND detail->>'entity_a_id' = $1
                  AND detail->>'entity_b_id' = $2
            """, a_id, b_id)

            if existing:
                continue

            name_a = names.get(a_id, "Unknown")
            name_b = names.get(b_id, "Unknown")

            await conn.execute("""
                INSERT INTO alerts (entity_id, alert_type, severity, title, detail)
                VALUES ($1::uuid, 'COORDINATED_POSTING', 'info', $2, $3)
            """, a_id,
                f"Repeated posting-time overlap: {name_a} <-> {name_b} ({occurrences} occurrences)",
                json.dumps({
                    "entity_a_id": a_id,
                    "entity_b_id": b_id,
                    "name_a": name_a,
                    "name_b": name_b,
                    "occurrences": occurrences,
                    "window_minutes": _env_int("COORDINATED_POSTING_WINDOW_MINUTES", 10),
                    "lookback_days": lookback_days,
                    "context_only": True,
                    "not_identity_evidence": True,
                }))
            count += 1

    return count


async def _detect_location_mismatches() -> int:
    """
    Phase 5B: LOCATION_MISMATCH.

    location_inference.infer_locations() stores
    behavioral_profiles.metadata.location_inference.source_countries as
    {"strava": "SG", "youtube": "US", ...}. If an entity has >= 2 distinct
    non-null country values across sources, flag a LOCATION_MISMATCH alert.

    Note: location_inference currently produces 0 results (no GPS/country
    data flowing yet), so this is expected to fire 0 alerts until source
    data is available — implemented to not error on the empty case.
    """
    pool = get_analyzer_pool()
    count = 0

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT bp.entity_id::text, bp.metadata, e.canonical_name
            FROM behavioral_profiles bp
            JOIN entities e ON e.id = bp.entity_id
            WHERE bp.metadata ? 'location_inference'
        """)

        for row in rows:
            meta = _decode_meta(row["metadata"])
            loc = meta.get("location_inference") or {}
            source_countries = loc.get("source_countries") or {}

            distinct_countries = {c for c in source_countries.values() if c}
            if len(distinct_countries) < 2:
                continue

            eid = row["entity_id"]
            country_list = ", ".join(f"{src}={c}" for src, c in sorted(source_countries.items()) if c)
            # sort_keys for stable comparison across runs regardless of dict insertion order
            source_countries_json = json.dumps(source_countries, default=str, sort_keys=True)

            existing = await conn.fetchval("""
                SELECT id FROM alerts
                WHERE alert_type = 'LOCATION_MISMATCH'
                  AND entity_id = $1
                  AND detail->>'source_countries_key' = $2
            """, eid, source_countries_json)

            if existing:
                continue

            name = row["canonical_name"] or "Unknown"
            await conn.execute("""
                INSERT INTO alerts (entity_id, alert_type, severity, title, detail)
                VALUES ($1::uuid, 'LOCATION_MISMATCH', 'info', $2, $3)
            """, eid,
                f"Location mismatch for {name}: {country_list}",
                json.dumps({
                    "entity_id": eid,
                    "source_countries": source_countries,
                    "source_countries_key": source_countries_json,
                    "distinct_countries": sorted(distinct_countries),
                }))
            count += 1

    return count
