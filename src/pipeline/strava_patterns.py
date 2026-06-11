import json
import logging
from collections import defaultdict

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)


async def analyze_strava_patterns() -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    async with collector.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.athlete_id, ath.username, a.name, a.type, a.sport_type,
                   a.distance, a.moving_time, a.elapsed_time, a.start_date,
                   a.total_elevation_gain, a.average_speed, a.average_heartrate
            FROM strava_activities a
            LEFT JOIN strava_athletes ath ON a.athlete_id = ath.id
            WHERE a.start_date IS NOT NULL
            ORDER BY a.athlete_id, a.start_date
        """)

    athletes: dict[str, list] = defaultdict(list)
    athlete_usernames: dict[str, str] = {}
    for r in rows:
        aid = str(r["athlete_id"]) if r["athlete_id"] else "unknown"
        athletes[aid].append(r)
        if r["username"]:
            athlete_usernames[aid] = r["username"]

    entity_lookup: dict[str, str] = {}
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, platform_username FROM entity_platform_links WHERE source = 'strava'"
        )
        for l in links:
            if l["platform_username"]:
                entity_lookup[l["platform_username"]] = l["entity_id"]

    stats = {"athletes_analyzed": 0, "patterns_found": 0}

    async with analyzer.acquire() as conn:
        for aid, activities in athletes.items():
            username = athlete_usernames.get(aid)
            entity_id = entity_lookup.get(username) if username else None

            if len(activities) < 3:
                continue

            type_counts: dict[str, int] = defaultdict(int)
            name_counts: dict[str, int] = defaultdict(int)
            hour_counts: dict[int, int] = defaultdict(int)
            dow_counts: dict[int, int] = defaultdict(int)
            distances: list[float] = []
            durations: list[int] = []

            for a in activities:
                atype = a["type"] or a["sport_type"] or "unknown"
                type_counts[atype] += 1
                name = a["name"] or ""
                if name and not _is_default_name(name):
                    name_counts[name] += 1
                if a["start_date"]:
                    hour_counts[a["start_date"].hour] += 1
                    dow_counts[a["start_date"].weekday()] += 1
                if a["distance"]:
                    distances.append(a["distance"])
                if a["moving_time"]:
                    durations.append(a["moving_time"])

            repeated_routes = {n: c for n, c in name_counts.items() if c >= 3}

            pattern = {
                "total_activities": len(activities),
                "activity_types": dict(type_counts),
                "preferred_hour": max(hour_counts, key=hour_counts.get) if hour_counts else None,
                "preferred_day": max(dow_counts, key=dow_counts.get) if dow_counts else None,
                "avg_distance_km": round(sum(distances) / len(distances) / 1000, 1) if distances else None,
                "avg_duration_min": round(sum(durations) / len(durations) / 60, 1) if durations else None,
                "repeated_routes": repeated_routes,
                "route_count": len(repeated_routes),
            }

            if entity_id:
                existing = await conn.fetchrow(
                    "SELECT id, metadata FROM behavioral_profiles WHERE entity_id = $1::uuid",
                    entity_id,
                )
                strava_json = json.dumps({"strava_patterns": pattern})
                if existing:
                    meta = existing["metadata"] if isinstance(existing["metadata"], dict) else {}
                    meta["strava_patterns"] = pattern
                    await conn.execute("""
                        UPDATE behavioral_profiles
                        SET metadata = $1::jsonb, updated_at = NOW()
                        WHERE entity_id = $2::uuid
                    """, json.dumps(meta, default=str), entity_id)
                else:
                    await conn.execute("""
                        INSERT INTO behavioral_profiles (entity_id, metadata)
                        VALUES ($1::uuid, $2::jsonb)
                        ON CONFLICT (entity_id) DO UPDATE SET
                            metadata = $2::jsonb, updated_at = NOW()
                    """, entity_id, json.dumps({"strava_patterns": pattern}, default=str))

                stats["patterns_found"] += 1

            stats["athletes_analyzed"] += 1

    logger.info("Strava pattern analysis: %s", stats)
    return stats


def _is_default_name(name: str) -> bool:
    defaults = {
        "morning run", "afternoon run", "evening run", "night run",
        "morning walk", "afternoon walk", "evening walk",
        "morning ride", "afternoon ride", "evening ride",
        "morning workout", "afternoon workout", "evening workout",
        "lunch run", "lunch walk", "lunch ride",
    }
    return name.lower().strip() in defaults
