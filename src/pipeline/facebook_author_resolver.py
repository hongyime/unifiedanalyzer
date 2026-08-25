"""Resolve Facebook post authors into Analyzer entities.

Collector Facebook posts often carry only `author_username`, while
`facebook_profiles` can be sparse. This resolver mirrors the Instagram content
resolver pattern: create an entity/link only for nonblank authors that have
collected content, and never move an existing platform link.
"""

from __future__ import annotations

import logging
import uuid as uuid_module

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

FACEBOOK_LINK_METHOD = "facebook_content"
FACEBOOK_LINK_CONFIDENCE = 0.62
SCAN_TIMEOUT_SECONDS = 180


_FACEBOOK_AUTHORS_WITH_CONTENT_SQL = """
    SELECT DISTINCT
        p.author_username AS author_username,
        COALESCE(fp.platform_user_id, p.author_username) AS platform_id,
        fp.display_name AS display_name
    FROM facebook_posts p
    LEFT JOIN facebook_profiles fp
      ON lower(fp.username) = lower(p.author_username)
    WHERE p.author_username IS NOT NULL
      AND p.author_username <> ''
"""


async def resolve_facebook_author_entities() -> dict:
    """Upsert entities and links for Facebook authors with collected posts."""
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    stats = {
        "substantive_authors": 0,
        "already_linked": 0,
        "entities_created": 0,
        "links_created": 0,
        "skipped_no_author": 0,
    }

    async with collector.acquire() as conn:
        rows = await conn.fetch(_FACEBOOK_AUTHORS_WITH_CONTENT_SQL, timeout=SCAN_TIMEOUT_SECONDS)

    stats["substantive_authors"] = len(rows)

    async with analyzer.acquire() as conn:
        existing = {
            r["platform_id"]
            for r in await conn.fetch(
                "SELECT platform_id FROM entity_platform_links WHERE source = 'facebook'"
            )
        }

    to_create: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for row in rows:
        author = (row["author_username"] or "").strip()
        platform_id = (row["platform_id"] or author).strip()
        if not author or not platform_id:
            stats["skipped_no_author"] += 1
            continue
        if platform_id in seen:
            continue
        seen.add(platform_id)
        if platform_id in existing:
            stats["already_linked"] += 1
            continue
        canonical = (row["display_name"] or "").strip() or author
        to_create.append((str(uuid_module.uuid4()), platform_id, author, canonical))

    if to_create:
        async with analyzer.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    """
                    INSERT INTO entities (id, tier, canonical_name,
                                          confidence_score, signal_count)
                    VALUES ($1::uuid, 'secondary', $2, $3, 0)
                    """,
                    [(entity_id, canonical, FACEBOOK_LINK_CONFIDENCE)
                     for entity_id, _platform_id, _author, canonical in to_create],
                )

                inserted = await conn.fetch(
                    """
                    INSERT INTO entity_platform_links
                        (entity_id, source, platform_id, platform_username,
                         platform_name, confidence, link_method, is_confirmed)
                    SELECT entity_id, 'facebook', platform_id, platform_username,
                           platform_name, $2, $3, FALSE
                    FROM UNNEST($1::uuid[], $4::text[], $5::text[], $6::text[])
                        AS t(entity_id, platform_id, platform_username, platform_name)
                    ON CONFLICT (source, platform_id) DO NOTHING
                    RETURNING entity_id::text
                    """,
                    [item[0] for item in to_create],
                    FACEBOOK_LINK_CONFIDENCE,
                    FACEBOOK_LINK_METHOD,
                    [item[1] for item in to_create],
                    [item[2] for item in to_create],
                    [item[3] for item in to_create],
                )

                linked_ids = {row["entity_id"] for row in inserted}
                stats["links_created"] = len(linked_ids)
                stats["entities_created"] = len(linked_ids)

                orphans = [item[0] for item in to_create if item[0] not in linked_ids]
                if orphans:
                    await conn.execute(
                        """
                        DELETE FROM entities
                        WHERE id = ANY($1::uuid[])
                          AND NOT EXISTS (
                            SELECT 1 FROM entity_platform_links l
                            WHERE l.entity_id = entities.id
                          )
                        """,
                        orphans,
                    )

    logger.info("Facebook author resolver complete: %s", stats)
    return stats
