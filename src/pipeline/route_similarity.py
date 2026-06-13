"""
Phase 4E: Strava route-origin similarity.

Detects entities that repeatedly start Strava activities from the same
physical location (home, workplace, gym — a "home base") as a same-person
signal.

Requires:
  - entity_platform_links with source='strava' to map Strava athletes to
    entities.
  - strava_activities.start_latlng populated. As of 2026-06 this is a
    collector-side GPS backfill still in progress (~5% coverage), so this
    pipeline is expected to produce few or no signals initially and pick up
    more on each incremental run as coverage grows — same "ready to fire"
    pattern as contact_extraction's phone_match/shared_website.

Signal: shared_route_origin
  - Both entities must have >= _MIN_OCCURRENCES activities starting within
    the same ~111m grid cell (3 decimal places of lat/lng) — i.e. a
    recurring "home base", not a one-off coincidence (e.g. both happened to
    start a run from the same MRT station once). A cluster shared by more
    than 2 entities is treated as a public location (park, popular trail)
    and skipped, same "fan-out" reasoning as cross_platform_link.
  - target_record_id convention: the other entity's UUID as text, consistent
    with content_similarity, temporal_copost, email_match, etc.
"""
import logging
from collections import defaultdict, Counter

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

_MIN_OCCURRENCES = 2  # cluster must recur at least this many times to count as a "home base"
_CLUSTER_PRECISION = 3  # decimal places of lat/lng (~111m grid cell)


def _cluster(latlng: str | None) -> tuple[float, float] | None:
    if not latlng:
        return None
    try:
        lat, lng = latlng.split(",")
        return (round(float(lat), _CLUSTER_PRECISION), round(float(lng), _CLUSTER_PRECISION))
    except (ValueError, AttributeError):
        return None


async def analyze_route_similarity() -> dict:
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    stats = {"entities_with_gps": 0, "shared_route_origin_signals": 0}

    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, platform_id FROM entity_platform_links WHERE source = 'strava'"
        )
    athlete_to_entity = {l["platform_id"]: l["entity_id"] for l in links}

    if not athlete_to_entity:
        return stats

    async with collector.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.platform_athlete_id::text AS pid, act.start_latlng
            FROM strava_activities act
            JOIN strava_athletes a ON act.athlete_id = a.id
            WHERE act.start_latlng IS NOT NULL
        """)

    entity_clusters: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        eid = athlete_to_entity.get(r["pid"])
        if not eid:
            continue
        cluster = _cluster(r["start_latlng"])
        if cluster:
            entity_clusters[eid][cluster] += 1

    stats["entities_with_gps"] = len(entity_clusters)

    # cluster -> entities for whom this is a recurring ("home base") location
    cluster_to_entities: dict[tuple[float, float], set[str]] = defaultdict(set)
    for eid, counter in entity_clusters.items():
        for cluster, count in counter.items():
            if count >= _MIN_OCCURRENCES:
                cluster_to_entities[cluster].add(eid)

    new_signals: list[tuple] = []
    for cluster, eids in cluster_to_entities.items():
        if len(eids) != 2:
            continue
        a, b = sorted(eids)
        lat, lng = cluster
        new_signals.append((
            a, "shared_route_origin", "strava", None, None, None,
            "strava", b, f"{lat},{lng}", 0.70,
        ))
        stats["shared_route_origin_signals"] += 1

    async with analyzer.acquire() as conn:
        await conn.execute(
            "DELETE FROM identity_signals WHERE signal_type = 'shared_route_origin'"
        )
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)

    logger.info("Route similarity: %s", stats)
    return stats
