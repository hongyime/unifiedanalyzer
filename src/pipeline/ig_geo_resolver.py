"""GAP-4 (VISION_PLAN "IG-geo profile resolution"): make collected Instagram
profiles first-class entities so their content — especially the 4,512
geo-tagged posts — surfaces on the map.

Two systemic collector-data facts drive this module:

  1. NULL-FK bug (matches the recurring "NULL FK hides data" hazard):
     ~23,519 of 23,555 `instagram_posts` rows — including ALL 4,512
     geo-tagged posts — have `profile_id = NULL`, so every consumer that
     attributes an IG post via `instagram_posts.profile_id ->
     instagram_profiles.id` (the timeline builder's IG block AND the
     `/geo` endpoint) silently drops them. The author's numeric id is
     nonetheless recoverable: `platform_post_id` has the shape
     `<media_id>_<author_user_id>`, and that trailing id equals
     `instagram_profiles.platform_user_id`. We backfill `profile_id` for
     the geo posts from that split so the existing (out-of-scope) API +
     timeline join starts attributing them — no API/timeline code change.

  2. The resolver only mints entities for target/corroborated profiles
     (SYNC #30), so most collected IG authors never get an
     `entity_platform_links(source='instagram')` row and are invisible
     graph-wide. The user's goal is "ALL entities we come across" (sparse
     OR rich). We therefore upsert an entity+link for every IG profile
     with SUBSTANTIVE collected content (a post, a recovered geo post,
     collected media of kind post/tagged/story, or an authored comment),
     keyed on the numeric `platform_user_id` per IDENTITY_KEYS.md, using
     the same safe "create-entity-if-content" pattern as beeper_bridge.py.

Safety / idempotency:
  * Links are upserted `ON CONFLICT (source, platform_id) DO NOTHING`, so
    a re-run never duplicates and never re-homes a link the resolver
    already owns (resolver precedence is preserved).
  * link_method='ig_content' — the resolver's stale-link cleanup only
    deletes link_method='auto' rows, so these survive resolution runs
    (same guarantee beeper_bridge relies on).
  * We ONLY create entities for profiles we actually collected content
    for; fleeting @mentions with no `instagram_profiles` row are skipped,
    so this does not explode the entity count with noise.
  * A pre-created entity whose link loses the ON CONFLICT race is deleted
    so no orphan lingers (mirrors beeper_bridge).

Wire order: run this AFTER resolve_entities()/bridge_beeper() (so those
own their profiles first) and BEFORE build_timeline() (so the timeline's
IG block sees the repaired profile_id and the fresh links).
"""

import logging
import uuid as uuid_module

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

IG_LINK_METHOD = "ig_content"
IG_LINK_CONFIDENCE = 0.7
# Backfill/scan touch the full instagram_posts table (~23k rows) plus the
# media/comment joins; give them headroom beyond the pool default.
SCAN_TIMEOUT_SECONDS = 300


# Recover the missing instagram_posts.profile_id for geo-tagged posts from the
# author id embedded in platform_post_id (`<media_id>_<author_user_id>`), so the
# out-of-scope timeline builder + /geo endpoint (both join on profile_id) start
# attributing the 4,512 geo posts. Scoped to geo posts (location_lat present) to
# keep the change tight to GAP-4; the same NULL-FK affects ~22k non-geo posts too
# (documented, not repaired here). Idempotent: only fills rows still NULL.
_BACKFILL_GEO_PROFILE_ID_SQL = """
    UPDATE instagram_posts p
    SET profile_id = ipf.id
    FROM instagram_profiles ipf
    WHERE p.profile_id IS NULL
      AND p.location_lat IS NOT NULL
      AND split_part(p.platform_post_id, '_', 2) ~ '^[0-9]+$'
      AND ipf.platform_user_id = split_part(p.platform_post_id, '_', 2)
"""


# Every IG profile with substantive collected content, keyed on the numeric
# platform_user_id (IDENTITY_KEYS.md). UNION dedupes across content types.
# media_items/comments key IG by USERNAME, so those legs join back through
# instagram_profiles to recover the canonical numeric id.
_SUBSTANTIVE_IG_PROFILES_SQL = """
    SELECT platform_user_id, username, full_name FROM instagram_profiles
    WHERE id IN (
        -- authored a collected post (incl. geo posts after the backfill above)
        SELECT DISTINCT profile_id FROM instagram_posts WHERE profile_id IS NOT NULL
    )
    UNION
    SELECT ipf.platform_user_id, ipf.username, ipf.full_name
    FROM instagram_profiles ipf
    WHERE ipf.platform_user_id IN (
        -- geo-post author recovered from platform_post_id (covers posts whose
        -- profile_id backfill did not stick, e.g. a race)
        SELECT DISTINCT split_part(platform_post_id, '_', 2)
        FROM instagram_posts
        WHERE location_lat IS NOT NULL
          AND split_part(platform_post_id, '_', 2) ~ '^[0-9]+$'
    )
    UNION
    SELECT ipf.platform_user_id, ipf.username, ipf.full_name
    FROM instagram_profiles ipf
    WHERE ipf.username IN (
        -- has collected media of a substantive kind (media_items keys IG by username)
        SELECT DISTINCT entity_id FROM media_items
        WHERE source = 'instagram' AND kind IN ('post', 'tagged', 'story')
    )
    UNION
    SELECT ipf.platform_user_id, ipf.username, ipf.full_name
    FROM instagram_profiles ipf
    WHERE ipf.username IN (
        -- authored a collected comment
        SELECT DISTINCT author_username FROM instagram_comments
        WHERE author_username IS NOT NULL AND author_username <> ''
    )
"""


async def resolve_ig_geo_entities() -> dict:
    """Backfill geo-post author FKs, then upsert an entity+link for every IG
    profile with substantive collected content. Returns run stats."""
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    stats = {
        "geo_profile_id_backfilled": 0,
        "substantive_profiles": 0,
        "already_linked": 0,
        "entities_created": 0,
        "links_created": 0,
        "skipped_no_uid": 0,
    }

    # ---- 1. Repair the NULL profile_id on geo posts ----------------------
    async with collector.acquire() as conn:
        res = await conn.execute(_BACKFILL_GEO_PROFILE_ID_SQL, timeout=SCAN_TIMEOUT_SECONDS)
        # res like "UPDATE 4202"
        try:
            stats["geo_profile_id_backfilled"] = int(res.split()[-1])
        except (ValueError, IndexError):
            stats["geo_profile_id_backfilled"] = 0

        profiles = await conn.fetch(_SUBSTANTIVE_IG_PROFILES_SQL, timeout=SCAN_TIMEOUT_SECONDS)

    stats["substantive_profiles"] = len(profiles)

    # ---- 2. Split into already-linked vs to-create -----------------------
    async with analyzer.acquire() as conn:
        existing = {
            r["platform_id"]
            for r in await conn.fetch(
                "SELECT platform_id FROM entity_platform_links WHERE source = 'instagram'"
            )
        }

    to_create: list[tuple[str, str, str | None, str]] = []
    for r in profiles:
        uid = r["platform_user_id"]
        if not uid:
            stats["skipped_no_uid"] += 1
            continue
        if uid in existing:
            stats["already_linked"] += 1
            continue
        username = r["username"]
        # canonical: distinctive full name > username > source:uid fallback so an
        # account is never "(unnamed)".
        canonical = (r["full_name"] or "").strip() or username or f"instagram:{uid}"
        to_create.append((str(uuid_module.uuid4()), uid, username, canonical))

    # ---- 3. Persist new entities + links (idempotent) --------------------
    if to_create:
        async with analyzer.acquire() as conn:
            async with conn.transaction():
                await conn.executemany("""
                    INSERT INTO entities (id, tier, canonical_name,
                                          confidence_score, signal_count)
                    VALUES ($1::uuid, 'secondary', $2, $3, 0)
                """, [(eid, canonical, IG_LINK_CONFIDENCE)
                      for eid, _, _, canonical in to_create])

                inserted = await conn.fetch("""
                    INSERT INTO entity_platform_links
                        (entity_id, source, platform_id, platform_username,
                         platform_name, confidence, link_method, is_confirmed)
                    SELECT entity_id, 'instagram', platform_id, platform_username,
                           platform_name, $2, $3, FALSE
                    FROM UNNEST($1::uuid[], $4::text[], $5::text[], $6::text[])
                        AS t(entity_id, platform_id, platform_username, platform_name)
                    ON CONFLICT (source, platform_id) DO NOTHING
                    RETURNING entity_id::text
                """,
                    [c[0] for c in to_create], IG_LINK_CONFIDENCE, IG_LINK_METHOD,
                    [c[1] for c in to_create], [c[2] for c in to_create],
                    [c[3] for c in to_create])

                linked_ids = {r["entity_id"] for r in inserted}
                stats["links_created"] = len(linked_ids)
                stats["entities_created"] = len(linked_ids)

                # Drop any entity whose link lost the ON CONFLICT race so no
                # orphan lingers until the resolver's cleanup (beeper pattern).
                orphans = [c[0] for c in to_create if c[0] not in linked_ids]
                if orphans:
                    await conn.execute(
                        "DELETE FROM entities WHERE id = ANY($1::uuid[]) "
                        "AND NOT EXISTS (SELECT 1 FROM entity_platform_links l "
                        "                WHERE l.entity_id = entities.id)",
                        orphans)

    logger.info("IG geo/content resolver complete: %s", stats)
    return stats
