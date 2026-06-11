import os
import asyncio
import logging
from datetime import datetime, timezone

from src.db.connection import check_db_connectivity, get_analyzer_pool, get_collector_pool
from src.pipeline.incremental_runner import run_incremental, run_full_resolution
from src.notifications.alerts import (
    notify_collector_health, notify_daily_digest, notify_merge_candidate,
)

logger = logging.getLogger(__name__)

_running = False


async def _check_collector_health() -> list[dict]:
    try:
        collector = get_collector_pool()
        async with collector.acquire() as conn:
            rows = await conn.fetch("""
                SELECT source,
                       MAX(started_at) AS last_run,
                       COUNT(*) FILTER (WHERE status = 'failed'
                           AND started_at > NOW() - INTERVAL '24 hours') AS failed_24h
                FROM collection_runs
                GROUP BY source
            """)
        issues = []
        now = datetime.now(timezone.utc)
        for r in rows:
            if r["last_run"]:
                hours_ago = (now - r["last_run"]).total_seconds() / 3600
                if hours_ago > 6:
                    issues.append({
                        "source": r["source"],
                        "message": f"no run in {hours_ago:.0f}h",
                    })
            if r["failed_24h"] and r["failed_24h"] >= 3:
                issues.append({
                    "source": r["source"],
                    "message": f"{r['failed_24h']} failures in 24h",
                })
        return issues
    except Exception:
        logger.debug("Collector health check failed", exc_info=True)
        return []


async def _build_daily_digest() -> dict:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entity_count = await conn.fetchval("SELECT COUNT(*) FROM entities")
        primary = await conn.fetchval("SELECT COUNT(*) FROM entities WHERE tier = 'primary'")
        secondary = await conn.fetchval("SELECT COUNT(*) FROM entities WHERE tier = 'secondary'")
        alerts_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM alerts WHERE detected_at > NOW() - INTERVAL '24 hours'"
        )
        unread = await conn.fetchval(
            "SELECT COUNT(*) FROM alerts WHERE is_read = false"
        )
        events_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM timeline_events WHERE created_at > NOW() - INTERVAL '24 hours'"
        )
        runs_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM analysis_runs WHERE started_at > NOW() - INTERVAL '24 hours'"
        )
        failed_runs = await conn.fetchval("""
            SELECT COUNT(*) FROM analysis_runs
            WHERE started_at > NOW() - INTERVAL '24 hours' AND status = 'failed'
        """)
        most_active = await conn.fetchrow("""
            SELECT e.canonical_name, COUNT(*) AS cnt
            FROM timeline_events te
            JOIN entities e ON te.entity_id = e.id
            WHERE te.created_at > NOW() - INTERVAL '24 hours'
            GROUP BY e.canonical_name
            ORDER BY cnt DESC LIMIT 1
        """)

    collector_issues = await _check_collector_health()

    return {
        "entity_count": entity_count,
        "primary": primary,
        "secondary": secondary,
        "alerts_24h": alerts_24h,
        "unread": unread,
        "events_24h": events_24h,
        "runs_24h": runs_24h,
        "failed_runs": failed_runs,
        "most_active": f"{most_active['canonical_name']} ({most_active['cnt']} events)"
            if most_active else None,
        "collectors_down": [i["source"] for i in collector_issues],
    }


async def _check_merge_candidates():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.entity_id, s.signal_type, s.value, s.confidence,
                   s.source_platform, s.target_platform,
                   e.canonical_name
            FROM identity_signals s
            JOIN entities e ON s.entity_id = e.id
            WHERE s.created_at > NOW() - INTERVAL '2 hours'
              AND s.confidence >= 15
        """)

        seen = set()
        for r in rows:
            entity_id = str(r["entity_id"])
            if entity_id in seen:
                continue
            seen.add(entity_id)

            other_entities = await conn.fetch("""
                SELECT DISTINCT e2.canonical_name, e2.id
                FROM identity_signals s2
                JOIN entities e2 ON s2.entity_id = e2.id
                WHERE s2.value = $1 AND s2.signal_type = $2
                  AND s2.entity_id != $3
            """, r["value"], r["signal_type"], r["entity_id"])

            for other in other_entities:
                await notify_merge_candidate(
                    r["canonical_name"] or "Unknown",
                    other["canonical_name"] or "Unknown",
                    r["confidence"] / 100,
                    r["signal_type"],
                )


async def start_scheduler() -> None:
    global _running
    _running = True

    interval = int(os.getenv("INCREMENTAL_RUN_INTERVAL_MINUTES", "60")) * 60
    full_hour = int(os.getenv("FULL_RESOLUTION_HOUR", "3"))
    digest_hour = int(os.getenv("DAILY_DIGEST_HOUR", "8"))

    logger.info("Scheduler started: incremental every %d min, full resolution at %02d:00 UTC, digest at %02d:00 UTC",
                interval // 60, full_hour, digest_hour)

    last_full_date: str | None = None
    last_digest_date: str | None = None
    last_health_check: datetime | None = None
    was_offline = False

    while _running:
        if not await check_db_connectivity():
            if not was_offline:
                logger.warning("Database unreachable — scheduler pausing until connectivity returns")
                was_offline = True
            await asyncio.sleep(30)
            continue

        if was_offline:
            logger.info("Database connectivity restored — scheduler resuming")
            was_offline = False

        now = datetime.now(timezone.utc)

        # Daily digest
        if now.hour == digest_hour and last_digest_date != now.strftime("%Y-%m-%d"):
            try:
                digest = await _build_daily_digest()
                await notify_daily_digest(digest)
                last_digest_date = now.strftime("%Y-%m-%d")
            except Exception:
                logger.exception("Daily digest failed")

        # Collector health check (every 3 hours)
        if last_health_check is None or (now - last_health_check).total_seconds() > 10800:
            try:
                issues = await _check_collector_health()
                await notify_collector_health(issues)
                last_health_check = now
            except Exception:
                logger.exception("Collector health check failed")

        if now.hour == full_hour and last_full_date != now.strftime("%Y-%m-%d"):
            logger.info("Starting nightly full resolution")
            try:
                await run_full_resolution()
                last_full_date = now.strftime("%Y-%m-%d")
            except Exception:
                logger.exception("Full resolution failed")
        else:
            logger.info("Starting incremental run")
            try:
                await run_incremental()
            except Exception:
                logger.exception("Incremental run failed")

        # Check for merge candidates after each run
        try:
            await _check_merge_candidates()
        except Exception:
            logger.debug("Merge candidate check failed", exc_info=True)

        await asyncio.sleep(interval)


def stop_scheduler() -> None:
    global _running
    _running = False
