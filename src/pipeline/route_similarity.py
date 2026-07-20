"""
Phase 4E: Strava route-origin similarity.

Detects entities that repeatedly start Strava activities from the same
physical location (home, workplace, gym — a "home base") as a same-person /
strong-tie signal.

Requires:
  - entity_platform_links with source='strava' to map Strava athletes to
    entities.
  - strava_activities.start_latlng populated (a "lat,lng" text pair). As of
    2026-07 the collector has ~9,983 activities with start_latlng, but only 12
    of the entity-linked athletes have any GPS coverage, so this pipeline still
    produces few/no signals until more Strava athletes are resolved into
    entities (entity_resolver's job). It fires automatically as coverage grows.

Signal: shared_route_origin
  - An entity must have >= _MIN_OCCURRENCES activities starting within the same
    ~111m grid cell (3 decimal places of lat/lng) for that cell to count as a
    recurring "home base" for the entity — not a one-off coincidence.
  - A cell where >= 2 entities each have a recurring home base is a shared
    origin. We emit ONE directed shared_route_origin signal per unordered pair
    (a < b) that shares the cell, weighted by how strongly both anchor there
    (min of the two per-entity start counts → the co-occurrence weight).
  - A cell shared by > _MAX_ENTITIES_PER_CLUSTER entities is treated as a public
    location (park, popular trail, MRT station) and skipped, same "fan-out"
    reasoning as cross_platform_link — a strong personal tie should not come
    from a place hundreds of people start runs from.
  - target_record_id convention: the other entity's UUID as text, consistent
    with content_similarity, email_match, group_cooccurrence, etc. This lets
    relationship_intelligence.py promote the signal into the strong
    `shared_home_or_gym` entity_relationships edge.

DOWNSTREAM: relationship_intelligence.py reads these signals and rolls them up
into `entity_relationships.relationship_type='shared_home_or_gym'` (idempotent
DELETE-then-INSERT there). This module owns only the signal layer; keeping the
two split matches the sibling pattern (content_similarity → content_reuse,
shared_website → self_declared_link).
"""
import logging
from collections import defaultdict, Counter
from itertools import combinations

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

_MIN_OCCURRENCES = 2  # a cell must recur >= this many times to be a "home base"
_CLUSTER_PRECISION = 3  # decimal places of lat/lng (~111m grid cell)
_MAX_ENTITIES_PER_CLUSTER = 6  # above this a cell is a public location → skip


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

    stats = {
        "entities_with_gps": 0,
        "shared_clusters": 0,
        "shared_route_origin_signals": 0,
    }

    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, platform_id FROM entity_platform_links WHERE source = 'strava'"
        )
    # One athlete id can only map to one entity, but one entity can own several
    # athlete ids — map every athlete id to its owning entity.
    athlete_to_entity = {l["platform_id"]: l["entity_id"] for l in links}

    if not athlete_to_entity:
        # Still clear any stale signals so an emptied-out source doesn't leave
        # lingering edges (mirrors the sibling delete-then-insert pattern).
        async with analyzer.acquire() as conn:
            await conn.execute(
                "DELETE FROM identity_signals WHERE signal_type = 'shared_route_origin'"
            )
        return stats

    async with collector.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.platform_athlete_id::text AS pid, act.start_latlng
            FROM strava_activities act
            JOIN strava_athletes a ON act.athlete_id = a.id
            WHERE act.start_latlng IS NOT NULL AND act.start_latlng <> ''
        """)

    # entity -> Counter of start-cell -> number of activities started there.
    entity_clusters: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        eid = athlete_to_entity.get(r["pid"])
        if not eid:
            continue
        cluster = _cluster(r["start_latlng"])
        if cluster:
            entity_clusters[eid][cluster] += 1

    stats["entities_with_gps"] = len(entity_clusters)

    # cell -> {entity_id: recurring_start_count} for entities anchored here.
    cluster_to_entities: dict[tuple[float, float], dict[str, int]] = defaultdict(dict)
    for eid, counter in entity_clusters.items():
        for cluster, count in counter.items():
            if count >= _MIN_OCCURRENCES:
                cluster_to_entities[cluster][eid] = count

    # For each pair of entities, keep the strongest shared cell (highest
    # co-occurrence weight) so a pair anchored at multiple shared cells is
    # represented once with its best evidence — matches the "max per pair"
    # dedupe used by other signal emitters.
    pair_best: dict[tuple[str, str], tuple[float, float, int]] = {}
    # value: (weight, lat, lng) plus we track weight only via tuple[0]
    for cluster, ent_counts in cluster_to_entities.items():
        eids = list(ent_counts.keys())
        if len(eids) < 2:
            continue
        if len(eids) > _MAX_ENTITIES_PER_CLUSTER:
            # Public location — too many distinct entities start here to be a
            # personal home/gym tie. Skip (fan-out guard).
            continue
        stats["shared_clusters"] += 1
        lat, lng = cluster
        for a, b in combinations(sorted(eids), 2):
            # Co-occurrence weight = the weaker of the two anchors (both must
            # genuinely recur here for it to be a shared home base).
            weight = min(ent_counts[a], ent_counts[b])
            key = (a, b)
            cur = pair_best.get(key)
            if cur is None or weight > cur[0]:
                pair_best[key] = (weight, lat, lng)

    # Build signal rows. Confidence scales with co-occurrence weight so the
    # downstream shared_home_or_gym edge weight reflects how strongly the pair
    # co-anchors: base 0.70, +0.03 per extra shared start, capped at 0.95.
    new_signals: list[tuple] = []
    for (a, b), (weight, lat, lng) in pair_best.items():
        confidence = round(min(0.95, 0.70 + (weight - _MIN_OCCURRENCES) * 0.03), 4)
        new_signals.append((
            a, "shared_route_origin", "strava", None, None, None,
            "strava", b, f"{lat},{lng}", confidence,
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
