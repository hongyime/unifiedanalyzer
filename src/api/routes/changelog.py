"""Entity "what changed since last viewed" feed (Option B).

Additions come free from the analyzer's existing created_at timestamps. Deletions
come from the collector's deletion tracking on the 3 messaging platforms
(telegram/whatsapp/beeper) — flagged with a deleted_at, no re-scraping. A
per-entity entity_views.last_viewed_at marker defines "since when"; the changelog
persists until the user hits "Mark reviewed".

deleted_at is WHEN WE OBSERVED the deletion (collector caveat), good enough for
"what changed". Only messages we already captured can be flagged.
"""
from fastapi import APIRouter

from src.db.connection import get_analyzer_pool, get_collector_pool

router = APIRouter(tags=["changelog"])

_DEFAULT_WINDOW = "30 days"


@router.get("/entities/{entity_id}/changelog")
async def entity_changelog(entity_id: str):
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    async with analyzer.acquire() as conn:
        since = await conn.fetchval(
            f"SELECT COALESCE((SELECT last_viewed_at FROM entity_views WHERE entity_id = $1::uuid),"
            f" NOW() - INTERVAL '{_DEFAULT_WINDOW}')", entity_id,
        )
        new_links = await conn.fetch("""
            SELECT source, platform_username, platform_id, created_at
            FROM entity_platform_links
            WHERE entity_id = $1::uuid AND created_at > $2
            ORDER BY created_at DESC LIMIT 25
        """, entity_id, since)
        new_events = await conn.fetchval("""
            SELECT count(*) FROM timeline_events WHERE entity_id = $1::uuid AND created_at > $2
        """, entity_id, since) or 0
        # NOTE: identity_signals are deliberately NOT counted — they're
        # deleted+recomputed every run, so their created_at always looks "new"
        # (it'd be noise, not a real change).
        new_alerts = await conn.fetch("""
            SELECT alert_type, title, detail, detected_at
            FROM alerts WHERE entity_id = $1::uuid AND detected_at > $2
            ORDER BY detected_at DESC LIMIT 15
        """, entity_id, since)
        # this entity's messaging accounts -> sender_id per platform
        links = await conn.fetch(
            "SELECT source, platform_id FROM entity_platform_links "
            "WHERE entity_id = $1::uuid AND source IN ('telegram','whatsapp','beeper')", entity_id,
        )

    tg_ids = [l["platform_id"] for l in links if l["source"] == "telegram" and l["platform_id"]]
    wa_ids = [l["platform_id"] for l in links if l["source"] == "whatsapp" and l["platform_id"]]
    bp_ids = [l["platform_id"] for l in links if l["source"] == "beeper" and l["platform_id"]]

    deletions: list[dict] = []
    async with collector.acquire() as cc:
        if tg_ids:
            rows = await cc.fetch("""
                SELECT text, (metadata->>'deleted_at')::timestamptz AS deleted_at
                FROM telegram_messages
                WHERE sender_id::text = ANY($1::text[]) AND metadata->>'deleted' = 'true'
                  AND (metadata->>'deleted_at')::timestamptz > $2
                ORDER BY deleted_at DESC LIMIT 30
            """, tg_ids, since)
            deletions += [{"platform": "telegram", "text": r["text"],
                           "deleted_at": r["deleted_at"].isoformat() if r["deleted_at"] else None} for r in rows]
        if wa_ids:
            rows = await cc.fetch("""
                SELECT text, deleted_at FROM whatsapp_messages
                WHERE sender_id::text = ANY($1::text[]) AND is_deleted = true AND deleted_at > $2
                ORDER BY deleted_at DESC LIMIT 30
            """, wa_ids, since)
            deletions += [{"platform": "whatsapp", "text": r["text"],
                           "deleted_at": r["deleted_at"].isoformat() if r["deleted_at"] else None} for r in rows]
        if bp_ids:
            rows = await cc.fetch("""
                SELECT text, deleted_at FROM beeper_shadow_messages
                WHERE sender_id::text = ANY($1::text[]) AND is_deleted = true AND deleted_at > $2
                ORDER BY deleted_at DESC LIMIT 30
            """, bp_ids, since)
            deletions += [{"platform": "beeper", "text": r["text"],
                           "deleted_at": r["deleted_at"].isoformat() if r["deleted_at"] else None} for r in rows]

    additions = {
        "platform_links": [{"source": r["source"], "username": r["platform_username"] or r["platform_id"],
                            "at": r["created_at"].isoformat() if r["created_at"] else None} for r in new_links],
        "timeline_events": new_events,
        "alerts": [{"alert_type": r["alert_type"], "title": r["title"], "detail": r["detail"],
                    "at": r["detected_at"].isoformat() if r["detected_at"] else None} for r in new_alerts],
    }
    total = (len(additions["platform_links"]) + new_events
             + len(additions["alerts"]) + len(deletions))
    return {
        "since": since.isoformat() if since else None,
        "additions": additions,
        "deletions": deletions,
        "total_changes": total,
    }


@router.post("/entities/{entity_id}/mark-reviewed")
async def mark_reviewed(entity_id: str):
    """Stamp last_viewed_at = now() so the changelog clears."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO entity_views (entity_id, last_viewed_at) VALUES ($1::uuid, NOW())
            ON CONFLICT (entity_id) DO UPDATE SET last_viewed_at = NOW()
        """, entity_id)
    return {"ok": True}
