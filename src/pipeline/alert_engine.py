import os
import json
import logging
from datetime import datetime, timedelta, timezone

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool = True) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _env_float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


async def run_alerts() -> dict:
    stats = {"silence_gap": 0, "new_activity": 0, "profile_change": 0}

    if _env_bool("SILENCE_GAP_DYNAMIC", True):
        stats["silence_gap"] = await _detect_silence_gaps()

    if _env_bool("NEW_ACTIVITY_AFTER_SILENCE_ENABLED", True):
        stats["new_activity"] = await _detect_new_activity_after_silence()

    if _env_bool("PROFILE_CHANGE_ALERT_ENABLED", True):
        stats["profile_change"] = await _detect_profile_changes()

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
            SELECT e.id, e.canonical_name
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

            if dynamic and history_days >= min_history and event_count >= 2:
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
