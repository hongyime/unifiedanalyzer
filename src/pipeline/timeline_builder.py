import json
import logging
from datetime import datetime, timedelta, timezone

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000
SOURCE_QUERY_TIMEOUT_SECONDS = 1800

# Bogus-timestamp floor. Source records occasionally carry epoch-0 (1970) or
# clock-error dates (e.g. GitHub commits with a misconfigured author clock dated
# 1985). Git itself dates to 2005, and this dataset has no legitimate pre-2005
# activity, so events before this are dropped rather than persisted — otherwise
# they land as permanent 1970/198x noise in the timeline (and its partition
# default). Deterministic, so it never removes a legit event.
TIMELINE_MIN_DATE = datetime(2005, 1, 1, tzinfo=timezone.utc)
# Future ceiling: an event can't legitimately be more than a small clock-skew
# margin into the future. Records dated years ahead (seen up to 2042) are clock
# errors — drop them too rather than seed far-future partition noise.
TIMELINE_MAX_FUTURE = timedelta(days=366)

PLATFORM_QUERIES = [
    {
        "source": "github",
        "event_type": "CODE_COMMIT",
        "query": """
            SELECT c.sha AS record_id, c.date AS occurred_at,
                   LEFT(c.message, 200) AS title, c.author_login AS entity_ref
            FROM github_commits c
            WHERE c.date IS NOT NULL {where_clause}
            ORDER BY c.date DESC
        """,
        "time_col": "c.date",
    },
    {
        "source": "strava",
        "event_type": "PHYSICAL_ACTIVITY",
        "query": """
            SELECT a.platform_activity_id::text AS record_id, a.start_date AS occurred_at,
                   a.name AS title, ath.username AS entity_ref
            FROM strava_activities a
            LEFT JOIN strava_athletes ath ON a.athlete_id = ath.id
            WHERE a.start_date IS NOT NULL {where_clause}
            ORDER BY a.start_date DESC
        """,
        "time_col": "a.start_date",
    },
    {
        "source": "instagram",
        "event_type": "CONTENT_PUBLISHED",
        "query": """
            SELECT p.platform_post_id AS record_id, p.platform_created_at AS occurred_at,
                   LEFT(p.caption, 200) AS title,
                   pr.platform_user_id::text AS entity_ref, pr.username AS entity_ref2,
                   CASE
                       WHEN p.location_lat IS NOT NULL
                         OR p.location_lng IS NOT NULL
                         OR p.location_name IS NOT NULL
                       THEN jsonb_strip_nulls(jsonb_build_object(
                           'location_lat', p.location_lat,
                           'location_lng', p.location_lng,
                           'location_name', p.location_name
                       ))
                       ELSE '{{}}'::jsonb
                   END AS metadata
            FROM instagram_posts p
            LEFT JOIN instagram_profiles pr ON p.profile_id = pr.id
            WHERE p.platform_created_at IS NOT NULL {where_clause}
            ORDER BY p.platform_created_at DESC
        """,
        "time_col": "p.platform_created_at",
    },
    {
        "source": "telegram",
        "event_type": "REACTION_GIVEN",
        "query": """
            SELECT r.id::text AS record_id, r.added_at AS occurred_at,
                   LEFT(COALESCE(m.text, m.caption, ''), 200) AS title,
                   actor.platform_user_id AS entity_ref, actor.username AS entity_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'emoji', r.emoji,
                       'target_message_id', m.platform_message_id,
                       'target_platform_user_id', target.platform_user_id,
                       'target_username', target.username,
                       'target_preview', LEFT(COALESCE(m.text, m.caption, ''), 200)
                   )) AS metadata
            FROM telegram_reactions r
            LEFT JOIN telegram_users actor ON actor.id = r.user_id
            LEFT JOIN telegram_messages m ON m.id = r.message_id
            LEFT JOIN telegram_users target ON target.id = m.sender_id
            WHERE r.added_at IS NOT NULL {where_clause}
            ORDER BY r.added_at DESC
        """,
        "time_col": "r.added_at",
    },
    {
        "source": "telegram",
        "event_type": "REPLIED",
        "query": """
            SELECT m.platform_message_id AS record_id, m.platform_created_at AS occurred_at,
                   LEFT(COALESCE(m.text, m.caption, ''), 200) AS title,
                   actor.platform_user_id AS entity_ref, actor.username AS entity_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'reply_to_message_id', m.reply_to_message_id,
                       'target_platform_user_id', target.platform_user_id,
                       'target_username', target.username,
                       'target_preview', LEFT(COALESCE(parent.text, parent.caption, ''), 200)
                   )) AS metadata
            FROM telegram_messages m
            LEFT JOIN telegram_users actor ON actor.id = m.sender_id
            LEFT JOIN telegram_chats chat ON chat.id = m.chat_id
            LEFT JOIN telegram_messages parent
              ON parent.platform_message_id = chat.platform_chat_id || ':' || m.reply_to_message_id
            LEFT JOIN telegram_users target ON target.id = parent.sender_id
            WHERE m.reply_to_message_id IS NOT NULL
              AND m.platform_created_at IS NOT NULL {where_clause}
            ORDER BY m.platform_created_at DESC
        """,
        "time_col": "m.platform_created_at",
    },
    {
        "source": "telegram",
        "event_type": "MESSAGE_SENT",
        # entity_ref = platform_user_id (matches entity_platform_links.platform_id,
        # the stable numeric Telegram id) with username as fallback. Previously
        # this matched on username ALONE, which is NULL for most senders — only
        # ~1.2% of 1.28M telegram events were attributed. (2026-07-10 attribution fix)
        "query": """
            SELECT m.platform_message_id AS record_id, m.platform_created_at AS occurred_at,
                   LEFT(m.text, 200) AS title,
                   u.platform_user_id AS entity_ref, u.username AS entity_ref2
            FROM telegram_messages m
            LEFT JOIN telegram_users u ON m.sender_id = u.id
            WHERE m.platform_created_at IS NOT NULL {where_clause}
            ORDER BY m.platform_created_at DESC
        """,
        "time_col": "m.platform_created_at",
    },
    {
        "source": "telegram",
        "event_type": "FORWARDED_MESSAGE",
        "query": """
            SELECT m.platform_message_id AS record_id, m.platform_created_at AS occurred_at,
                   LEFT(COALESCE(m.text, m.caption, ''), 200) AS title,
                   actor.platform_user_id AS entity_ref, actor.username AS entity_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'forward_from_chat_id', m.forward_from_chat_id,
                       'forward_from_message_id', m.forward_from_message_id,
                       'message_preview', LEFT(COALESCE(m.text, m.caption, ''), 200)
                   )) AS metadata
            FROM telegram_messages m
            LEFT JOIN telegram_users actor ON actor.id = m.sender_id
            WHERE (m.forward_from_chat_id IS NOT NULL OR m.forward_from_message_id IS NOT NULL)
              AND m.platform_created_at IS NOT NULL {where_clause}
            ORDER BY m.platform_created_at DESC
        """,
        "time_col": "m.platform_created_at",
    },
    {
        "source": "whatsapp",
        "event_type": "MESSAGE_SENT",
        # entity_ref = reconstructed JID (phone_number || '@s.whatsapp.net'), which
        # is what entity_platform_links.platform_id stores for WhatsApp; bare
        # phone_number is the fallback. Previously matched on u.name (display name),
        # which never matches a JID key — 0% of 50.7k whatsapp events were
        # attributed despite 637 whatsapp entities. (2026-07-10 attribution fix)
        # @lid senders (group messages) have NULL phone_number -> the old
        # phone||'@s.whatsapp.net' produced NULL and never attributed (~78% of wa
        # msgs). Resolve @lid -> full phone JID via whatsapp_lid_map (lid=u.platform_user_id,
        # phone_jid is already a full JID). COALESCE to the phone fallback. (SYNC #32)
        "query": """
            SELECT m.platform_message_id AS record_id, m.timestamp AS occurred_at,
                   LEFT(m.text, 200) AS title,
                   COALESCE(lm.phone_jid, u.phone_number || '@s.whatsapp.net') AS entity_ref,
                   COALESCE(split_part(lm.phone_jid, '@', 1), u.phone_number) AS entity_ref2
            FROM whatsapp_messages m
            LEFT JOIN whatsapp_users u ON m.sender_id = u.id
            LEFT JOIN whatsapp_lid_map lm ON lm.lid = u.platform_user_id
            WHERE m.timestamp IS NOT NULL {where_clause}
            ORDER BY m.timestamp DESC
        """,
        "time_col": "m.timestamp",
    },
    {
        "source": "tiktok",
        "event_type": "CONTENT_PUBLISHED",
        "query": """
            SELECT p.platform_post_id AS record_id, p.create_time AS occurred_at,
                   LEFT(p.title, 200) AS title, pr.username AS entity_ref
            FROM tiktok_posts p
            LEFT JOIN tiktok_profiles pr ON p.profile_id = pr.id
            WHERE p.create_time IS NOT NULL {where_clause}
            ORDER BY p.create_time DESC
        """,
        "time_col": "p.create_time",
    },
    {
        "source": "youtube",
        "event_type": "VIDEO_PUBLISHED",
        # entity_ref = platform_channel_id (the UC... id in
        # entity_platform_links.platform_id) with custom_url (@handle) fallback.
        # Previously matched on ch.title (display name) — 0% of 22.4k youtube
        # events were attributed despite 491 youtube entities. (2026-07-10 fix)
        "query": """
            SELECT v.platform_video_id AS record_id, v.platform_published_at AS occurred_at,
                   v.title AS title,
                   ch.platform_channel_id AS entity_ref, ch.custom_url AS entity_ref2
            FROM youtube_videos v
            LEFT JOIN youtube_channels ch ON v.channel_id = ch.id
            WHERE v.platform_published_at IS NOT NULL {where_clause}
            ORDER BY v.platform_published_at DESC
        """,
        "time_col": "v.platform_published_at",
    },
    {
        "source": "lemon8",
        "event_type": "CONTENT_PUBLISHED",
        "query": """
            SELECT p.platform_post_id AS record_id,
                   COALESCE(p.platform_created_at, p.collected_at) AS occurred_at,
                   LEFT(p.title, 200) AS title, pr.username AS entity_ref
            FROM lemon8_posts p
            LEFT JOIN lemon8_profiles pr ON p.profile_id = pr.id
            WHERE COALESCE(p.platform_created_at, p.collected_at) IS NOT NULL {where_clause}
            ORDER BY COALESCE(p.platform_created_at, p.collected_at) DESC
        """,
        "time_col": "COALESCE(p.platform_created_at, p.collected_at)",
    },
    {
        "source": "threads",
        "event_type": "CONTENT_PUBLISHED",
        # entity_ref = author_username; entity_platform_links.platform_id for
        # threads/x is the handle (threads=IG handle). SYNC #34 — entity links
        # for these were added by SYNC #30; this attributes their posts.
        "query": """
            SELECT p.platform_post_id AS record_id,
                   COALESCE(p.platform_created_at, p.collected_at) AS occurred_at,
                   LEFT(p.caption, 200) AS title, p.author_username AS entity_ref
            FROM threads_posts p
            WHERE COALESCE(p.platform_created_at, p.collected_at) IS NOT NULL {where_clause}
            ORDER BY COALESCE(p.platform_created_at, p.collected_at) DESC
        """,
        "time_col": "COALESCE(p.platform_created_at, p.collected_at)",
    },
    {
        "source": "x",
        "event_type": "CONTENT_PUBLISHED",
        "query": """
            SELECT p.platform_post_id AS record_id,
                   COALESCE(p.platform_created_at, p.collected_at) AS occurred_at,
                   LEFT(p.caption, 200) AS title, p.author_username AS entity_ref
            FROM x_posts p
            WHERE COALESCE(p.platform_created_at, p.collected_at) IS NOT NULL {where_clause}
            ORDER BY COALESCE(p.platform_created_at, p.collected_at) DESC
        """,
        "time_col": "COALESCE(p.platform_created_at, p.collected_at)",
    },
    {
        "source": "github",
        "event_type": "ISSUE_OPENED",
        "query": """
            SELECT i.platform_issue_id::text AS record_id, i.created_at AS occurred_at,
                   i.title AS title, NULL AS entity_ref
            FROM github_issues i
            WHERE i.created_at IS NOT NULL {where_clause}
            ORDER BY i.created_at DESC
        """,
        "time_col": "i.created_at",
    },
    {
        "source": "instagram",
        "event_type": "COMMENT_POSTED",
        "query": """
            SELECT c.platform_comment_id AS record_id, c.platform_created_at AS occurred_at,
                   LEFT(c.text, 200) AS title,
                   c.author_platform_id AS entity_ref, c.author_username AS entity_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'post_id', p.platform_post_id,
                       'owner_platform_user_id', pr.platform_user_id,
                       'owner_username', pr.username,
                       'parent_comment_id', c.parent_comment_id,
                       'is_reply', c.is_reply
                   )) AS metadata
            FROM instagram_comments c
            LEFT JOIN instagram_posts p ON p.id = c.post_id
            LEFT JOIN instagram_profiles pr ON pr.id = p.profile_id
            WHERE c.platform_created_at IS NOT NULL {where_clause}
            ORDER BY c.platform_created_at DESC
        """,
        "time_col": "c.platform_created_at",
    },
    {
        "source": "tiktok",
        "event_type": "COMMENT_POSTED",
        "query": """
            SELECT c.platform_comment_id AS record_id, c.platform_created_at AS occurred_at,
                   LEFT(c.text, 200) AS title,
                   c.author_username AS entity_ref,
                   jsonb_strip_nulls(jsonb_build_object(
                       'post_id', p.platform_post_id,
                       'owner_platform_user_id', pr.platform_user_id,
                       'owner_username', pr.username,
                       'parent_comment_id', c.parent_comment_id,
                       'reply_count', c.reply_count
                   )) AS metadata
            FROM tiktok_comments c
            LEFT JOIN tiktok_posts p ON p.id = c.post_id
            LEFT JOIN tiktok_profiles pr ON pr.id = p.profile_id
            WHERE c.platform_created_at IS NOT NULL {where_clause}
            ORDER BY c.platform_created_at DESC
        """,
        "time_col": "c.platform_created_at",
    },
    {
        "source": "youtube",
        "event_type": "COMMENT_POSTED",
        "query": """
            SELECT c.platform_comment_id AS record_id, c.platform_published_at AS occurred_at,
                   LEFT(c.text_original, 200) AS title,
                   c.author_channel_id AS entity_ref, c.author_name AS entity_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'video_id', v.platform_video_id,
                       'owner_platform_channel_id', ch.platform_channel_id,
                       'owner_handle', ch.custom_url,
                       'parent_comment_id', c.parent_comment_id,
                       'is_reply', c.is_reply
                   )) AS metadata
            FROM youtube_comments c
            LEFT JOIN youtube_videos v ON v.id = c.video_id
            LEFT JOIN youtube_channels ch ON ch.id = v.channel_id
            WHERE c.platform_published_at IS NOT NULL {where_clause}
            ORDER BY c.platform_published_at DESC
        """,
        "time_col": "c.platform_published_at",
    },
    {
        "source": "strava",
        "event_type": "COMMENT_POSTED",
        "query": """
            SELECT c.id::text AS record_id, c.platform_created_at AT TIME ZONE 'UTC' AS occurred_at,
                   LEFT(c.comment_text, 200) AS title,
                   c.platform_athlete_id::text AS entity_ref, c.athlete_name AS entity_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'activity_id', a.platform_activity_id,
                       'owner_platform_athlete_id', owner.platform_athlete_id,
                       'owner_username', owner.username
                   )) AS metadata
            FROM strava_activity_comments c
            LEFT JOIN strava_activities a ON a.platform_activity_id = c.platform_activity_id
            LEFT JOIN strava_athletes owner ON owner.id = a.athlete_id
            WHERE c.platform_created_at IS NOT NULL {where_clause}
            ORDER BY c.platform_created_at DESC
        """,
        "time_col": "c.platform_created_at",
    },
    {
        "source": "instagram",
        "event_type": "FOLLOWED",
        "query": """
            SELECT CONCAT_WS(':', f.platform, f.owner_account, f.target_uid, f.direction) AS record_id,
                   COALESCE(f.first_seen, f.last_seen) AS occurred_at,
                   COALESCE(f.target_username, f.target_uid) AS title,
                   f.owner_account AS entity_ref,
                   jsonb_strip_nulls(jsonb_build_object(
                       'target_uid', f.target_uid,
                       'target_username', f.target_username,
                       'direction', f.direction,
                       'first_seen', f.first_seen,
                       'last_seen', f.last_seen
                   )) AS metadata
            FROM follow_edges f
            WHERE f.platform = 'instagram'
              AND f.direction = 'following'
              AND COALESCE(f.first_seen, f.last_seen) IS NOT NULL {where_clause}
            ORDER BY COALESCE(f.first_seen, f.last_seen) DESC
        """,
        "time_col": "COALESCE(f.first_seen, f.last_seen)",
    },
    {
        "source": "instagram",
        "event_type": "STORY_POSTED",
        "query": """
            SELECT m.id::text AS record_id,
                   COALESCE(
                       CASE
                           WHEN COALESCE(m.metadata->>'taken_at', '') ~ '^[0-9]+(\\.[0-9]+)?$'
                           THEN to_timestamp((m.metadata->>'taken_at')::double precision)
                       END,
                       m.created_at,
                       m.collected_at
                   ) AS occurred_at,
                   LEFT(COALESCE(NULLIF(m.metadata->>'caption', ''), m.entity_name, m.content_id), 200) AS title,
                   m.entity_id AS entity_ref, m.entity_name AS entity_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'kind', m.kind,
                       'content_id', m.content_id,
                       'likes_count', NULLIF(m.metadata->>'likes_count', ''),
                       'comments_count', NULLIF(m.metadata->>'comments_count', ''),
                       'views_count', NULLIF(m.metadata->>'views_count', ''),
                       'caption', NULLIF(m.metadata->>'caption', ''),
                       'source_url', m.source_url,
                       'timestamp_source', CASE
                           WHEN COALESCE(m.metadata->>'taken_at', '') ~ '^[0-9]+(\\.[0-9]+)?$' THEN 'metadata.taken_at'
                           WHEN m.created_at IS NOT NULL THEN 'created_at'
                           ELSE 'collected_at'
                       END
                   )) AS metadata
            FROM media_items m
            WHERE m.source = 'instagram'
              AND m.kind = 'story'
              AND COALESCE(
                    CASE
                        WHEN COALESCE(m.metadata->>'taken_at', '') ~ '^[0-9]+(\\.[0-9]+)?$'
                        THEN to_timestamp((m.metadata->>'taken_at')::double precision)
                    END,
                    m.created_at,
                    m.collected_at
                  ) IS NOT NULL {where_clause}
            ORDER BY occurred_at DESC
        """,
        "time_col": """
            COALESCE(
                CASE
                    WHEN COALESCE(m.metadata->>'taken_at', '') ~ '^[0-9]+(\\.[0-9]+)?$'
                    THEN to_timestamp((m.metadata->>'taken_at')::double precision)
                END,
                m.created_at,
                m.collected_at
            )
        """,
    },
    {
        "source": "instagram",
        "event_type": "HIGHLIGHT_POSTED",
        "query": """
            SELECT m.id::text AS record_id,
                   COALESCE(
                       CASE
                           WHEN COALESCE(m.metadata->>'taken_at', '') ~ '^[0-9]+(\\.[0-9]+)?$'
                           THEN to_timestamp((m.metadata->>'taken_at')::double precision)
                       END,
                       m.created_at,
                       m.collected_at
                   ) AS occurred_at,
                   LEFT(COALESCE(NULLIF(m.metadata->>'caption', ''), m.entity_name, m.content_id), 200) AS title,
                   m.entity_id AS entity_ref, m.entity_name AS entity_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'kind', m.kind,
                       'content_id', m.content_id,
                       'likes_count', NULLIF(m.metadata->>'likes_count', ''),
                       'comments_count', NULLIF(m.metadata->>'comments_count', ''),
                       'views_count', NULLIF(m.metadata->>'views_count', ''),
                       'caption', NULLIF(m.metadata->>'caption', ''),
                       'source_url', m.source_url,
                       'timestamp_source', CASE
                           WHEN COALESCE(m.metadata->>'taken_at', '') ~ '^[0-9]+(\\.[0-9]+)?$' THEN 'metadata.taken_at'
                           WHEN m.created_at IS NOT NULL THEN 'created_at'
                           ELSE 'collected_at'
                       END
                   )) AS metadata
            FROM media_items m
            WHERE m.source = 'instagram'
              AND m.kind = 'highlight'
              AND COALESCE(
                    CASE
                        WHEN COALESCE(m.metadata->>'taken_at', '') ~ '^[0-9]+(\\.[0-9]+)?$'
                        THEN to_timestamp((m.metadata->>'taken_at')::double precision)
                    END,
                    m.created_at,
                    m.collected_at
                  ) IS NOT NULL {where_clause}
            ORDER BY occurred_at DESC
        """,
        "time_col": """
            COALESCE(
                CASE
                    WHEN COALESCE(m.metadata->>'taken_at', '') ~ '^[0-9]+(\\.[0-9]+)?$'
                    THEN to_timestamp((m.metadata->>'taken_at')::double precision)
                END,
                m.created_at,
                m.collected_at
            )
        """,
    },
    {
        "source": "instagram",
        "event_type": "TAGGED_IN",
        "query": """
            SELECT m.id::text AS record_id,
                   COALESCE(m.created_at, m.collected_at) AS occurred_at,
                   LEFT(COALESCE(m.entity_name, 'Tagged photo'), 200) AS title,
                   m.entity_name AS entity_ref,
                   m.entity_id AS entity_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'content_id', m.content_id,
                       'media_id', NULLIF(split_part(m.content_id, '_', 2), ''),
                       'owner_platform_user_id', NULLIF(split_part(m.content_id, '_', 3), ''),
                       'owner_username', su.username,
                       'source_url', m.source_url,
                       'likes_count', NULLIF(m.metadata->>'likes_count', ''),
                       'comments_count', NULLIF(m.metadata->>'comments_count', ''),
                       'views_count', NULLIF(m.metadata->>'views_count', '')
                   )) AS metadata
            FROM media_items m
            LEFT JOIN social_users su
              ON su.platform = 'instagram'
             AND su.platform_user_id = NULLIF(split_part(m.content_id, '_', 3), '')
            WHERE m.source = 'instagram'
              AND m.kind = 'tagged'
              AND m.entity_name IS NOT NULL
              AND m.entity_name <> ''
              AND COALESCE(m.created_at, m.collected_at) IS NOT NULL {where_clause}
            ORDER BY COALESCE(m.created_at, m.collected_at) DESC
        """,
        "time_col": "COALESCE(m.created_at, m.collected_at)",
    },
    {
        "source": "facetracker",
        "event_type": "PHOTO_COAPPEARANCE",
        "db": "analyzer",
        "entity_ids_direct": True,
        "query": """
            SELECT CONCAT(er.id::text, ':a') AS record_id,
                   COALESCE(er.last_seen_at, er.created_at) AS occurred_at,
                   CONCAT('Co-appeared in a photo with ', COALESCE(other_e.canonical_name, er.entity_b_id::text)) AS title,
                   er.entity_a_id::text AS entity_ref,
                   jsonb_strip_nulls(jsonb_build_object(
                       'relationship_id', er.id,
                       'other_entity_id', er.entity_b_id,
                       'other_entity_name', other_e.canonical_name,
                       'confidence', er.weight,
                       'why', er.sources->>'why'
                   )) AS metadata
            FROM entity_relationships er
            LEFT JOIN entities other_e ON other_e.id = er.entity_b_id
            WHERE er.relationship_type = 'mutual_social_face'
              AND COALESCE(er.last_seen_at, er.created_at) IS NOT NULL {where_clause}
            UNION ALL
            SELECT CONCAT(er.id::text, ':b') AS record_id,
                   COALESCE(er.last_seen_at, er.created_at) AS occurred_at,
                   CONCAT('Co-appeared in a photo with ', COALESCE(other_e.canonical_name, er.entity_a_id::text)) AS title,
                   er.entity_b_id::text AS entity_ref,
                   jsonb_strip_nulls(jsonb_build_object(
                       'relationship_id', er.id,
                       'other_entity_id', er.entity_a_id,
                       'other_entity_name', other_e.canonical_name,
                       'confidence', er.weight,
                       'why', er.sources->>'why'
                   )) AS metadata
            FROM entity_relationships er
            LEFT JOIN entities other_e ON other_e.id = er.entity_a_id
            WHERE er.relationship_type = 'mutual_social_face'
              AND COALESCE(er.last_seen_at, er.created_at) IS NOT NULL {where_clause}
            ORDER BY occurred_at DESC
        """,
        "time_col": "COALESCE(er.last_seen_at, er.created_at)",
    },
]


async def _get_entity_lookup() -> dict[tuple[str, str], str]:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT entity_id::text, source, platform_id, platform_username
            FROM entity_platform_links
        """)
    lookup: dict[tuple[str, str], str] = {}
    for r in rows:
        if r["platform_username"]:
            lookup[(r["source"], r["platform_username"].lower())] = r["entity_id"]
        lookup[(r["source"], r["platform_id"])] = r["entity_id"]
    return lookup


async def ensure_timeline_partitions(months_ahead: int = 6) -> None:
    """P2-5 maintenance: guarantee a monthly partition exists for the current
    month and the next `months_ahead`, so newly-built events never silently fall
    into the DEFAULT partition. No-op when timeline_events isn't partitioned
    (fresh/pre-migration installs). Idempotent and cheap (catalog lookups)."""
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        is_part = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_partitioned_table pt
                JOIN pg_class c ON c.oid = pt.partrelid
                WHERE c.relname = 'timeline_events'
            )
        """)
        if not is_part:
            return
        base = datetime.now(timezone.utc)
        for i in range(months_ahead + 1):
            y = base.year + (base.month - 1 + i) // 12
            mo = (base.month - 1 + i) % 12 + 1
            ny, nmo = (y + 1, 1) if mo == 12 else (y, mo + 1)
            name = f"timeline_events_{y:04d}_{mo:02d}"
            if await conn.fetchval("SELECT to_regclass($1)", f"public.{name}") is not None:
                continue
            try:
                await conn.execute(
                    f"CREATE TABLE {name} PARTITION OF timeline_events "
                    f"FOR VALUES FROM ('{y:04d}-{mo:02d}-01') TO ('{ny:04d}-{nmo:02d}-01')"
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {name}_entity_attributed_time_idx "
                    f"ON {name}(entity_id, occurred_at DESC) WHERE entity_id IS NOT NULL"
                )
                logger.info("Created timeline partition %s", name)
            except Exception:
                # e.g. a stray out-of-range row already sits in DEFAULT for this
                # month — non-fatal; the row stays in default, new ones still work.
                logger.debug("Could not create partition %s (non-fatal)", name, exc_info=True)


async def build_timeline(
    since: datetime | None = None,
    skip_sources: set[str] | None = None,
    only_sources: set[str] | None = None,
    only_event_types: set[str] | None = None,
) -> dict:
    """Build/refresh timeline_events from the collector.

    since: only pull collector rows newer than this (incremental). None = full
        rescan, used by full_resolution to RE-ATTRIBUTE events whose entity was
        resolved after the event was first built.
    skip_sources: platform sources to skip entirely this pass. The full-rescan
        re-attribution is O(all collector rows), dominated by github's ~7.3M
        commits — but github attribution is a hard ceiling (~6 tracked github
        entities) so reprocessing it every 12h yields nothing while pinning a
        CPU on a cursor loop for tens of minutes. The full run passes
        {"github"} here; NEW github events are still picked up by the ordinary
        2-hourly incremental (since=last_run, no skip). (2026-07-10 perf fix)
    """
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()
    skip_sources = skip_sources or set()
    only_sources = only_sources or set()
    only_event_types = only_event_types or set()
    # Keep upcoming monthly partitions provisioned before inserting.
    try:
        await ensure_timeline_partitions()
    except Exception:
        logger.debug("ensure_timeline_partitions failed (non-fatal)", exc_info=True)
    entity_lookup = await _get_entity_lookup()

    stats = {
        "total": 0,
        "processed": 0,
        "inserted": 0,
        "attributed": 0,
        "unresolved": 0,
        "skipped_invalid_time": 0,
        "skipped_tables": [],
        "by_source": {},
    }

    for pq in PLATFORM_QUERIES:
        if pq["source"] in skip_sources:
            stats["skipped_tables"].append(f"{pq['source']}/{pq['event_type']} (skip_sources)")
            continue
        if only_sources and pq["source"] not in only_sources:
            continue
        if only_event_types and pq["event_type"] not in only_event_types:
            continue
        where_clause = ""
        params: list = []
        if since:
            where_clause = f"AND {pq['time_col']} > $1"
            if pq.get("db") == "analyzer" or since.tzinfo is None:
                params.append(since)
            else:
                params.append(since.astimezone(timezone.utc).replace(tzinfo=None))

        query = pq["query"].format(where_clause=where_clause)
        source_name = f"{pq['source']}/{pq['event_type']}"

        try:
            pool = analyzer if pq.get("db") == "analyzer" else collector
            async with pool.acquire() as conn:
                async with conn.transaction():
                    cursor = conn.cursor(query, *params, timeout=SOURCE_QUERY_TIMEOUT_SECONDS)

                    batch: list[tuple] = []
                    row_count = 0
                    attributed_count = 0
                    unresolved_count = 0
                    invalid_time_count = 0

                    async for row in cursor:
                        # Try the primary entity_ref then an optional fallback
                        # (entity_ref2) — some sources match on an id column but
                        # keep a handle/username as a secondary key (see the
                        # per-source query comments). First ref that resolves wins.
                        entity_id = None
                        if pq.get("entity_ids_direct"):
                            entity_ref = row.get("entity_ref")
                            entity_id = str(entity_ref) if entity_ref else None
                        else:
                            for _ref_col in ("entity_ref", "entity_ref2"):
                                entity_ref = row.get(_ref_col)
                                if not entity_ref:
                                    continue
                                entity_ref = str(entity_ref)
                                entity_id = entity_lookup.get((pq["source"], entity_ref.lower())) \
                                    or entity_lookup.get((pq["source"], entity_ref))
                                if entity_id:
                                    break

                        occurred_at = row["occurred_at"]
                        if occurred_at and not occurred_at.tzinfo:
                            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                        # Skip null/bogus timestamps so they don't regenerate as
                        # 1970/198x (or far-future) timeline noise.
                        if not occurred_at or occurred_at < TIMELINE_MIN_DATE:
                            invalid_time_count += 1
                            continue
                        if occurred_at > datetime.now(timezone.utc) + TIMELINE_MAX_FUTURE:
                            invalid_time_count += 1
                            continue

                        batch.append((
                            entity_id,
                            pq["source"],
                            pq["event_type"],
                            str(row["record_id"]),
                            occurred_at,
                            row.get("title"),
                            _jsonb_param(row.get("metadata")),
                        ))
                        row_count += 1
                        if entity_id:
                            attributed_count += 1
                        else:
                            unresolved_count += 1

                        if len(batch) >= BATCH_SIZE:
                            await _insert_batch(analyzer, batch)
                            stats["inserted"] += len(batch)
                            batch.clear()

                    if batch:
                        await _insert_batch(analyzer, batch)
                        stats["inserted"] += len(batch)

                    stats["total"] += row_count
                    stats["processed"] += row_count
                    stats["attributed"] += attributed_count
                    stats["unresolved"] += unresolved_count
                    stats["skipped_invalid_time"] += invalid_time_count
                    stats["by_source"][source_name] = {
                        "processed": row_count,
                        "attributed": attributed_count,
                        "unresolved": unresolved_count,
                        "skipped_invalid_time": invalid_time_count,
                    }
                    logger.info(
                        "Timeline %s: %d rows processed (%d attributed, %d unresolved)",
                        source_name,
                        row_count,
                        attributed_count,
                        unresolved_count,
                    )

        except Exception as e:
            logger.warning("Skipping %s: %s", source_name, e)
            stats["skipped_tables"].append(source_name)

    logger.info("Timeline build complete: %s", stats)
    return stats


async def _insert_batch(pool, batch: list[tuple]) -> None:
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO timeline_events
                (entity_id, source, event_type, source_record_id, occurred_at, title, metadata)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb)
            -- P2-5: 4-col conflict target (includes occurred_at) so the upsert
            -- works on the month-partitioned table, where every unique key must
            -- include the partition column. occurred_at is deterministic per
            -- (source,event_type,source_record_id), so this preserves the old
            -- 3-col semantics. Backed by idx_timeline_uniq4.
            ON CONFLICT (source, event_type, source_record_id, occurred_at)
            DO UPDATE SET
                entity_id = COALESCE(EXCLUDED.entity_id, timeline_events.entity_id),
                title = EXCLUDED.title,
                metadata = EXCLUDED.metadata
            -- Only write when the attribution actually changes. Without this
            -- guard every conflicting row is rewritten on each full rebuild,
            -- churning millions of dead tuples and growing the C:-backed
            -- Postgres volume for no reason. IS DISTINCT FROM handles NULLs on
            -- both sides. Metadata/title updates are also allowed so newly-added
            -- source payloads can backfill existing timeline rows.
            WHERE (
                    EXCLUDED.entity_id IS NOT NULL
                    AND timeline_events.entity_id IS DISTINCT FROM EXCLUDED.entity_id
                  )
               OR timeline_events.title IS DISTINCT FROM EXCLUDED.title
               OR timeline_events.metadata IS DISTINCT FROM EXCLUDED.metadata
        """, batch)


def _jsonb_param(raw) -> str:
    if raw is None:
        return "{}"
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, default=str)


async def repair_replied_metadata(batch_size: int = 2000, max_batches: int | None = None) -> dict:
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()
    stats = {"scanned": 0, "updated": 0, "resolved_targets": 0}
    last_occurred_at = None
    last_source_record_id = None
    batches_run = 0

    while True:
        if max_batches is not None and batches_run >= max_batches:
            break
        async with analyzer.acquire() as aconn:
            rows = await aconn.fetch(
                """
                SELECT source_record_id, occurred_at
                FROM timeline_events
                WHERE source = 'telegram'
                  AND event_type = 'REPLIED'
                  AND (
                    $1::timestamptz IS NULL
                    OR (occurred_at, source_record_id) < ($1::timestamptz, $2::text)
                  )
                ORDER BY occurred_at DESC, source_record_id DESC
                LIMIT $3
                """,
                last_occurred_at,
                last_source_record_id,
                batch_size,
                timeout=SOURCE_QUERY_TIMEOUT_SECONDS,
            )
        if not rows:
            break

        batch_ids = [row["source_record_id"] for row in rows]
        updated, resolved = await _repair_replied_batch(batch_ids, collector, analyzer)
        stats["scanned"] += len(batch_ids)
        stats["updated"] += updated
        stats["resolved_targets"] += resolved
        last_occurred_at = rows[-1]["occurred_at"]
        last_source_record_id = rows[-1]["source_record_id"]
        batches_run += 1

    logger.info("Replied metadata repair complete: %s", stats)
    return stats


async def _repair_replied_batch(
    source_ids: list[str],
    collector_pool,
    analyzer_pool,
) -> tuple[int, int]:
    async with collector_pool.acquire() as cconn:
        rows = await cconn.fetch(
            """
            SELECT m.platform_message_id,
                   jsonb_strip_nulls(jsonb_build_object(
                       'reply_to_message_id', m.reply_to_message_id,
                       'target_platform_user_id', target.platform_user_id,
                       'target_username', target.username,
                       'target_preview', LEFT(COALESCE(parent.text, parent.caption, ''), 200),
                       'reply_repair_checked', true,
                       'reply_target_resolved', CASE
                           WHEN target.platform_user_id IS NOT NULL OR target.username IS NOT NULL
                           THEN true
                           ELSE false
                       END
                   )) AS metadata,
                   CASE WHEN target.platform_user_id IS NOT NULL OR target.username IS NOT NULL THEN 1 ELSE 0 END AS resolved
            FROM telegram_messages m
            LEFT JOIN telegram_chats chat ON chat.id = m.chat_id
            LEFT JOIN telegram_messages parent
              ON parent.platform_message_id = chat.platform_chat_id || ':' || m.reply_to_message_id
            LEFT JOIN telegram_users target ON target.id = parent.sender_id
            WHERE m.platform_message_id = ANY($1::text[])
            """,
            source_ids,
            timeout=SOURCE_QUERY_TIMEOUT_SECONDS,
        )
    payloads = [(
        row["platform_message_id"],
        _jsonb_param(row["metadata"]),
    ) for row in rows]
    if not payloads:
        return 0, 0
    resolved = sum(int(row["resolved"] or 0) for row in rows)
    async with analyzer_pool.acquire() as aconn:
        result = await aconn.execute(
            """
            UPDATE timeline_events AS te
            SET metadata = COALESCE(te.metadata, '{}'::jsonb) || payload.metadata
            FROM unnest($1::text[], $2::jsonb[]) AS payload(source_record_id, metadata)
            WHERE te.source = 'telegram'
              AND te.event_type = 'REPLIED'
              AND te.source_record_id = payload.source_record_id
              AND te.metadata IS DISTINCT FROM (COALESCE(te.metadata, '{}'::jsonb) || payload.metadata)
            """,
            [row[0] for row in payloads],
            [row[1] for row in payloads],
            timeout=SOURCE_QUERY_TIMEOUT_SECONDS,
        )
    updated = int(result.split()[-1]) if result.startswith("UPDATE ") else 0
    return updated, resolved
