import os
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from src.db.connection import get_analyzer_pool

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
    }

    if _env_bool("SILENCE_GAP_DYNAMIC", True):
        stats["silence_gap"] = await _detect_silence_gaps()

    if _env_bool("NEW_ACTIVITY_AFTER_SILENCE_ENABLED", True):
        stats["new_activity"] = await _detect_new_activity_after_silence()

    if _env_bool("PROFILE_CHANGE_ALERT_ENABLED", True):
        stats["profile_change"] = await _detect_profile_changes()

    if _env_bool("NEW_IDENTITY_LINK_ALERT_ENABLED", True):
        stats["new_identity_link"] = await _detect_new_identity_links()

    if _env_bool("COORDINATED_POSTING_ALERT_ENABLED", True):
        stats["coordinated_posting"] = await _detect_coordinated_posting()

    if _env_bool("LOCATION_MISMATCH_ALERT_ENABLED", True):
        stats["location_mismatch"] = await _detect_location_mismatches()

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

    now = datetime.now(timezone.utc)
    count = 0

    async with pool.acquire() as conn:
        entities = await conn.fetch("""
            SELECT e.id, e.canonical_name, e.silence_threshold_days
            FROM entities e
            WHERE e.tier = 'primary'
        """)

        for entity in entities:
            eid = entity["id"]

            row = await conn.fetchrow("""
                SELECT
                    MAX(occurred_at) AS last_event,
                    MIN(occurred_at) AS first_event,
                    COUNT(*) AS event_count
                FROM timeline_events
                WHERE entity_id = $1
            """, eid)

            if not row or not row["last_event"]:
                continue

            last_event = row["last_event"]
            first_event = row["first_event"]
            event_count = row["event_count"]
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
    threshold_weight = _env_int("NEW_IDENTITY_LINK_MIN_WEIGHT", 70)
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
              AND er.weight >= $1
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
    count = 0

    now = datetime.now(timezone.utc)
    since = max(_EPOCH_FLOOR, now - timedelta(days=lookback_days))

    async with pool.acquire() as conn:
        events = await conn.fetch("""
            SELECT entity_id::text, occurred_at, source
            FROM timeline_events
            WHERE entity_id IS NOT NULL
              AND occurred_at > $1
            ORDER BY occurred_at
        """, since)

        # entity_id -> sorted list of (occurred_at, source)
        entity_events: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        for e in events:
            entity_events[e["entity_id"]].append((e["occurred_at"], e["source"]))

        entity_ids = list(entity_events)

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
                VALUES ($1::uuid, 'COORDINATED_POSTING', 'warning', $2, $3)
            """, a_id,
                f"Coordinated cross-platform posting: {name_a} <-> {name_b} ({occurrences} occurrences)",
                json.dumps({
                    "entity_a_id": a_id,
                    "entity_b_id": b_id,
                    "name_a": name_a,
                    "name_b": name_b,
                    "occurrences": occurrences,
                    "window_minutes": _env_int("COORDINATED_POSTING_WINDOW_MINUTES", 10),
                    "lookback_days": lookback_days,
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
