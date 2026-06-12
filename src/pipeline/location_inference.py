"""
Phase 4A: Location inference.

Aggregates geographic signals from multiple sources per entity:
  - strava_athletes.city/state/country  (available now)
  - youtube_channels.country            (available now)
  - strava_activities.timezone          (once scraper backfills)
  - instagram_posts.location_name/lat/lng (once posts collected)
  - behavioral_profiles.inferred_timezone (already computed)

Stores location_inference in behavioral_profiles.metadata.
"""
import re
import json
import logging
from collections import Counter

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

# Regex to extract IANA timezone from Strava format "(GMT+08:00) Asia/Singapore"
_TZ_RE = re.compile(r"\)\s+(.+)$")


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


def _parse_strava_timezone(tz_str: str | None) -> str | None:
    if not tz_str:
        return None
    m = _TZ_RE.search(tz_str)
    return m.group(1).strip() if m else None


def _parse_latlng(raw) -> tuple[float, float] | None:
    """Parse '[lat, lng]' string or list into (lat, lng) tuple."""
    if not raw:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            return (float(raw[0]), float(raw[1]))
        except (ValueError, TypeError):
            return None
    if isinstance(raw, str):
        raw = raw.strip()
        if raw in ("", "[]", "null"):
            return None
        raw = raw.strip("[]")
        parts = raw.split(",")
        if len(parts) == 2:
            try:
                return (float(parts[0].strip()), float(parts[1].strip()))
            except ValueError:
                return None
    return None


def _latlng_to_region(lat: float, lng: float) -> str | None:
    """Coarse region bucketing without a geocoding library."""
    if lat is None or lng is None:
        return None
    # Southeast Asia
    if 1 <= lat <= 28 and 95 <= lng <= 145:
        return "SEA"
    # East Asia
    if 20 <= lat <= 55 and 100 <= lng <= 145:
        return "East Asia"
    # South Asia
    if 5 <= lat <= 37 and 60 <= lng <= 97:
        return "South Asia"
    # Europe
    if 35 <= lat <= 72 and -25 <= lng <= 45:
        return "Europe"
    # North America
    if 15 <= lat <= 72 and -170 <= lng <= -50:
        return "North America"
    # Oceania
    if -50 <= lat <= -10 and 110 <= lng <= 180:
        return "Oceania"
    # Middle East / West Asia
    if 12 <= lat <= 42 and 32 <= lng <= 75:
        return "Middle East"
    # Africa
    if -35 <= lat <= 38 and -20 <= lng <= 55:
        return "Africa"
    # South America
    if -55 <= lat <= 15 and -82 <= lng <= -32:
        return "South America"
    return None


async def infer_locations() -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    # --- Build entity lookup ---
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id FROM entity_platform_links"
        )
        pid_to_entity: dict[tuple[str, str], str] = {
            (l["source"], l["platform_id"]): l["entity_id"] for l in links
        }

        existing_rows = await conn.fetch(
            "SELECT entity_id::text, metadata FROM behavioral_profiles"
        )
        existing_meta: dict[str, dict] = {}
        for row in existing_rows:
            m = _decode_meta(row["metadata"])
            if m:
                existing_meta[row["entity_id"]] = m

    # --- Collect location signals per entity ---
    # entity_id → {countries: Counter, timezones: Counter, cities: list, regions: Counter}
    signals: dict[str, dict] = {}

    def _ensure(eid: str) -> dict:
        if eid not in signals:
            signals[eid] = {"countries": Counter(), "timezones": Counter(),
                            "cities": [], "regions": Counter(), "location_names": [],
                            "source_countries": {}}
        return signals[eid]

    async with collector.acquire() as conn:
        # 1. Strava athlete home location
        try:
            rows = await conn.fetch("""
                SELECT a.platform_athlete_id::text AS platform_id,
                       a.city, a.state, a.country
                FROM strava_athletes a
                WHERE a.country IS NOT NULL AND a.country != ''
            """)
            for r in rows:
                eid = pid_to_entity.get(("strava", r["platform_id"]))
                if not eid:
                    continue
                s = _ensure(eid)
                if r["country"]:
                    s["countries"][r["country"]] += 2
                    s["source_countries"]["strava"] = r["country"]
                city_parts = [p for p in [r["city"], r["state"]] if p]
                if city_parts:
                    s["cities"].append(", ".join(city_parts))
        except Exception:
            logger.debug("Strava athlete location failed", exc_info=True)

        # 2. Strava activity timezones
        try:
            rows = await conn.fetch("""
                SELECT ath.platform_athlete_id::text AS platform_id,
                       a.timezone, a.utc_offset,
                       a.start_latlng, a.start_latlng_full
                FROM strava_activities a
                JOIN strava_athletes ath ON a.athlete_id = ath.id
                WHERE a.timezone IS NOT NULL AND a.timezone != ''
                   OR a.start_latlng IS NOT NULL AND a.start_latlng != '' AND a.start_latlng != '[]'
            """)
            for r in rows:
                eid = pid_to_entity.get(("strava", r["platform_id"]))
                if not eid:
                    continue
                s = _ensure(eid)
                tz = _parse_strava_timezone(r["timezone"])
                if tz:
                    s["timezones"][tz] += 1
                coords = _parse_latlng(r["start_latlng"] or r["start_latlng_full"])
                if coords:
                    region = _latlng_to_region(*coords)
                    if region:
                        s["regions"][region] += 1
        except Exception:
            logger.debug("Strava activity timezone failed", exc_info=True)

        # 3. YouTube channel country
        try:
            rows = await conn.fetch("""
                SELECT platform_channel_id, country
                FROM youtube_channels
                WHERE country IS NOT NULL AND country != ''
            """)
            for r in rows:
                eid = pid_to_entity.get(("youtube", r["platform_channel_id"]))
                if not eid:
                    continue
                s = _ensure(eid)
                s["countries"][r["country"]] += 1
                s["source_countries"]["youtube"] = r["country"]
        except Exception:
            logger.debug("YouTube channel country failed", exc_info=True)

        # 4. Instagram post locations (when available)
        try:
            rows = await conn.fetch("""
                SELECT ip.platform_user_id AS platform_id,
                       p.location_name, p.location_lat, p.location_lng
                FROM instagram_posts p
                JOIN instagram_profiles ip ON p.profile_id = ip.id
                WHERE p.location_name IS NOT NULL AND p.location_name != ''
                   OR p.location_lat IS NOT NULL
            """)
            for r in rows:
                eid = pid_to_entity.get(("instagram", r["platform_id"]))
                if not eid:
                    continue
                s = _ensure(eid)
                if r["location_name"]:
                    s["location_names"].append(r["location_name"])
                if r["location_lat"] and r["location_lng"]:
                    region = _latlng_to_region(float(r["location_lat"]), float(r["location_lng"]))
                    if region:
                        s["regions"][region] += 1
        except Exception:
            logger.debug("Instagram post location failed", exc_info=True)

        # 5. Lemon8 post locations
        try:
            rows = await conn.fetch("""
                SELECT lp.platform_user_id AS platform_id, p.location_name
                FROM lemon8_posts p
                JOIN lemon8_profiles lp ON p.profile_id = lp.id
                WHERE p.location_name IS NOT NULL AND p.location_name != ''
            """)
            for r in rows:
                eid = pid_to_entity.get(("lemon8", r["platform_id"]))
                if not eid:
                    continue
                _ensure(eid)["location_names"].append(r["location_name"])
        except Exception:
            logger.debug("Lemon8 location failed", exc_info=True)

    stats = {"entities_updated": 0, "entities_with_location": len(signals)}

    async with analyzer.acquire() as conn:
        for entity_id, s in signals.items():
            top_country = s["countries"].most_common(1)[0][0] if s["countries"] else None
            top_tz = s["timezones"].most_common(1)[0][0] if s["timezones"] else None
            top_region = s["regions"].most_common(1)[0][0] if s["regions"] else None
            unique_locations = list(dict.fromkeys(s["location_names"]))[:10]
            unique_cities = list(dict.fromkeys(s["cities"]))[:5]

            if not any([top_country, top_tz, top_region, unique_locations]):
                continue

            inference = {
                "primary_country": top_country,
                "primary_timezone": top_tz,
                "region": top_region,
                "location_names": unique_locations,
                "home_cities": unique_cities,
                "country_votes": dict(s["countries"].most_common(5)),
                "timezone_votes": dict(s["timezones"].most_common(5)),
                "source_countries": s["source_countries"],
            }

            existing = await conn.fetchrow(
                "SELECT id, metadata FROM behavioral_profiles WHERE entity_id = $1::uuid",
                entity_id,
            )
            if existing:
                meta = _decode_meta(existing["metadata"])
                meta["location_inference"] = inference
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
                """, entity_id, json.dumps({"location_inference": inference}, default=str))

            stats["entities_updated"] += 1

    logger.info("Location inference: %s", stats)
    return stats
