"""VISION_PLAN gap-round-2 ("Broaden strava entity resolution" → unblocks T5.2
shared-route-origin): make every collected Strava athlete with SUBSTANTIVE
collected content a first-class entity, so their GPS activity feeds
`route_similarity.py` → `shared_home_or_gym` edges.

The bottleneck this closes:

  The resolver (`entity_resolver.py`) only mints entities for target/corroborated
  profiles, so of the 1,424 Strava athletes that actually have GPS-tagged
  activities, only ~72 ever get an `entity_platform_links(source='strava')` row.
  `route_similarity.analyze_route_similarity()` can therefore only cluster start
  cells across those 72 athletes and finds no shared home base → 0
  `shared_route_origin` signals → 0 `shared_home_or_gym` edges (T5.2 blocked
  upstream, VISION_PLAN 2026-07-15).

The fix mirrors the proven `ig_geo_resolver.resolve_ig_geo_entities()` pattern
(GAP-4): create an entity + link for every Strava athlete row that has ≥1
collected `strava_activities` row — i.e. substantive collected content, not a
fleeting mention. Keyed on the numeric `platform_athlete_id` per
IDENTITY_KEYS.md (`source='strava'`, `platform_username`=`username`,
`link_method='strava_content'`).

Safety / idempotency (identical guarantees to ig_geo_resolver):
  * Links upserted `ON CONFLICT (source, platform_id) DO NOTHING`, so a re-run
    never duplicates and never re-homes a link the resolver already owns
    (resolver precedence preserved).
  * link_method='strava_content' — the resolver's stale-link cleanup only
    deletes link_method='auto' rows, so these survive resolution runs.
  * We ONLY create entities for athletes we actually collected activities for;
    the ~31k athletes with zero activities (discovery-only follower rows) are
    skipped, so this does not explode the entity count with noise.
  * A pre-created entity whose link loses the ON CONFLICT race is deleted so no
    orphan lingers (mirrors ig_geo_resolver / beeper_bridge).

Wire order: run AFTER resolve_entities()/bridge_beeper()/resolve_ig_geo_entities()
(so those own their profiles first) and BEFORE build_timeline() (so the timeline's
Strava blocks see the fresh links). route_similarity runs later in
_secondary_phases and auto-lights once coverage grows.
"""

import logging
import uuid as uuid_module

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

STRAVA_LINK_METHOD = "strava_content"
STRAVA_LINK_CONFIDENCE = 0.7
# The scan joins strava_athletes→strava_activities (activities is large); give it
# headroom beyond the pool default, matching ig_geo_resolver's SCAN_TIMEOUT.
SCAN_TIMEOUT_SECONDS = 300


# Every Strava athlete with ≥1 collected activity, keyed on the numeric
# platform_athlete_id (IDENTITY_KEYS.md). GPS-tagged athletes (start_latlng
# present) are the T5.2-relevant subset but ALL athletes with activities have
# substantive collected content and become entities — the fan-out guard in
# route_similarity handles public locations, so broad resolution here is safe and
# is exactly the "create-entity-if-content" contract.
_SUBSTANTIVE_STRAVA_ATHLETES_SQL = """
    SELECT DISTINCT a.platform_athlete_id::text AS platform_athlete_id,
           a.username,
           NULLIF(TRIM(CONCAT_WS(' ', a.firstname, a.lastname)), '') AS full_name
    FROM strava_athletes a
    WHERE EXISTS (
        SELECT 1 FROM strava_activities act WHERE act.athlete_id = a.id
    )
"""


async def resolve_strava_athlete_entities() -> dict:
    """Upsert an entity+link for every Strava athlete with ≥1 collected activity.
    Returns run stats. Idempotent + resolver-precedence-preserving."""
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    stats = {
        "substantive_athletes": 0,
        "already_linked": 0,
        "entities_created": 0,
        "links_created": 0,
        "skipped_no_id": 0,
    }

    # ---- 1. Athletes with substantive collected content ------------------
    async with collector.acquire() as conn:
        athletes = await conn.fetch(
            _SUBSTANTIVE_STRAVA_ATHLETES_SQL, timeout=SCAN_TIMEOUT_SECONDS
        )
    stats["substantive_athletes"] = len(athletes)

    # ---- 2. Split into already-linked vs to-create -----------------------
    async with analyzer.acquire() as conn:
        existing = {
            r["platform_id"]
            for r in await conn.fetch(
                "SELECT platform_id FROM entity_platform_links WHERE source = 'strava'"
            )
        }

    to_create: list[tuple[str, str, str | None, str]] = []
    for r in athletes:
        aid = r["platform_athlete_id"]
        if not aid:
            stats["skipped_no_id"] += 1
            continue
        if aid in existing:
            stats["already_linked"] += 1
            continue
        username = r["username"]
        # canonical: distinctive full name > username > source:id fallback so an
        # athlete is never "(unnamed)".
        canonical = (r["full_name"] or "").strip() or username or f"strava:{aid}"
        to_create.append((str(uuid_module.uuid4()), aid, username, canonical))

    # ---- 3. Persist new entities + links (idempotent) --------------------
    if to_create:
        async with analyzer.acquire() as conn:
            async with conn.transaction():
                await conn.executemany("""
                    INSERT INTO entities (id, tier, canonical_name,
                                          confidence_score, signal_count)
                    VALUES ($1::uuid, 'secondary', $2, $3, 0)
                """, [(eid, canonical, STRAVA_LINK_CONFIDENCE)
                      for eid, _, _, canonical in to_create])

                inserted = await conn.fetch("""
                    INSERT INTO entity_platform_links
                        (entity_id, source, platform_id, platform_username,
                         platform_name, confidence, link_method, is_confirmed)
                    SELECT entity_id, 'strava', platform_id, platform_username,
                           platform_name, $2, $3, FALSE
                    FROM UNNEST($1::uuid[], $4::text[], $5::text[], $6::text[])
                        AS t(entity_id, platform_id, platform_username, platform_name)
                    ON CONFLICT (source, platform_id) DO NOTHING
                    RETURNING entity_id::text
                """,
                    [c[0] for c in to_create], STRAVA_LINK_CONFIDENCE, STRAVA_LINK_METHOD,
                    [c[1] for c in to_create], [c[2] for c in to_create],
                    [c[3] for c in to_create])

                linked_ids = {r["entity_id"] for r in inserted}
                stats["links_created"] = len(linked_ids)
                stats["entities_created"] = len(linked_ids)

                # Drop any entity whose link lost the ON CONFLICT race so no
                # orphan lingers until the resolver's cleanup (ig_geo pattern).
                orphans = [c[0] for c in to_create if c[0] not in linked_ids]
                if orphans:
                    await conn.execute(
                        "DELETE FROM entities WHERE id = ANY($1::uuid[]) "
                        "AND NOT EXISTS (SELECT 1 FROM entity_platform_links l "
                        "                WHERE l.entity_id = entities.id)",
                        orphans)

    logger.info("Strava athlete resolver complete: %s", stats)
    return stats
