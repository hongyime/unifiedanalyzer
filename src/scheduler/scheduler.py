import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.db.connection import check_db_connectivity, get_analyzer_pool, get_collector_pool
from src.pipeline.incremental_runner import (
    run_incremental,
    run_full_resolution,
    get_last_run_time,
    clear_orphaned_run_locks,
)
from src.notifications.alerts import (
    notify_collector_health, notify_daily_digest, notify_merge_candidate,
    notify_status,
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
        try:
            faces_linked = await conn.fetchval("SELECT COUNT(*) FROM entity_faces")
        except Exception:
            faces_linked = 0
        try:
            failing_rows = await conn.fetch("""
                WITH latest AS (
                    SELECT DISTINCT ON (phase) phase, status
                    FROM run_phase_status ORDER BY phase, created_at DESC
                )
                SELECT phase FROM latest WHERE status = 'failed'
            """)
            failing_phases = [r["phase"] for r in failing_rows]
        except Exception:
            failing_phases = []

    collector_issues = await _check_collector_health()

    return {
        "entity_count": entity_count,
        "primary": primary,
        "secondary": secondary,
        "faces_linked": faces_linked or 0,
        "alerts_24h": alerts_24h,
        "unread": unread,
        "events_24h": events_24h,
        "runs_24h": runs_24h,
        "failed_runs": failed_runs,
        "failing_phases": failing_phases,
        "most_active": f"{most_active['canonical_name']} ({most_active['cnt']} events)"
            if most_active else None,
        "collectors_down": [i["source"] for i in collector_issues],
    }


async def _build_status() -> dict:
    """Compact current-state snapshot for the periodic status heartbeat.

    Reports ACCURATE live state: entity tier breakdown, faces DETECTED vs
    actually LINKED to entities (these differ by orders of magnitude — most
    detected faces are untracked web/search media), whether a run is in progress
    (with heartbeat freshness) vs the last completed run, any pipeline phases
    currently failing (run_phase_status), and quiet collector sources.
    """
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entity_count = await conn.fetchval("SELECT COUNT(*) FROM entities")
        primary = await conn.fetchval("SELECT COUNT(*) FROM entities WHERE tier = 'primary'")
        secondary = await conn.fetchval("SELECT COUNT(*) FROM entities WHERE tier = 'secondary'")
        alerts_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM alerts WHERE detected_at > NOW() - INTERVAL '24 hours'"
        )
        unread = await conn.fetchval("SELECT COUNT(*) FROM alerts WHERE is_read = false")
        # Faces live in the facetracker schema (merge); tolerate it being absent.
        # DETECTED = all faces in the corpus; LINKED = faces actually bridged to a
        # tracked entity (public.entity_faces). Reporting only the former is
        # misleading (it's dominated by untracked search/website media).
        try:
            faces_detected = await conn.fetchval("SELECT COUNT(*) FROM facetracker.faces")
        except Exception:
            faces_detected = 0
        try:
            faces_linked = await conn.fetchval("SELECT COUNT(*) FROM entity_faces")
        except Exception:
            faces_linked = 0

        # Current in-progress run (if any) + its heartbeat freshness.
        running = await conn.fetchrow(
            "SELECT run_type, started_at, heartbeat_at FROM analysis_runs "
            "WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        )
        last = await conn.fetchrow(
            "SELECT run_type, finished_at FROM analysis_runs "
            "WHERE status = 'completed' AND finished_at IS NOT NULL "
            "ORDER BY finished_at DESC LIMIT 1"
        )
        failed_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM analysis_runs "
            "WHERE status = 'failed' AND finished_at > NOW() - INTERVAL '24 hours'"
        )
        # Pipeline phases whose most-recent run failed (P2-3 run_phase_status).
        try:
            failing_rows = await conn.fetch("""
                WITH latest AS (
                    SELECT DISTINCT ON (phase) phase, status
                    FROM run_phase_status ORDER BY phase, created_at DESC
                )
                SELECT phase FROM latest WHERE status = 'failed'
            """)
            failing_phases = [r["phase"] for r in failing_rows]
        except Exception:
            failing_phases = []

    now = datetime.now(timezone.utc)
    run_state = None
    if running:
        mins = int((now - running["started_at"]).total_seconds() // 60)
        hb = running["heartbeat_at"]
        hb_age = int((now - hb).total_seconds()) if hb else None
        hb_str = f", heartbeat {hb_age}s ago" if hb_age is not None else ""
        run_state = f"running {running['run_type']} ({mins}m{hb_str})"
    elif last and last["finished_at"]:
        run_state = f"idle · last {last['run_type']} @ {last['finished_at'].strftime('%Y-%m-%d %H:%M UTC')}"

    issues = await _check_collector_health()
    return {
        "db_ok": True,
        "entity_count": entity_count or 0,
        "primary": primary or 0,
        "secondary": secondary or 0,
        "faces_detected": faces_detected or 0,
        "faces_linked": faces_linked or 0,
        "alerts_24h": alerts_24h or 0,
        "unread": unread or 0,
        "run_state": run_state,
        "failed_runs_24h": failed_24h or 0,
        "failing_phases": failing_phases,
        "collectors_down": [i["source"] for i in issues],
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

    # A fresh scheduler process is the only scheduler — clear any 'running' lock
    # orphaned by a predecessor killed mid-run (container recreate), so we don't
    # skip runs for up to 30 min waiting on the stale-lock timer.
    try:
        await clear_orphaned_run_locks()
    except Exception:
        logger.exception("Failed clearing orphaned run locks at startup (non-fatal)")

    interval = int(os.getenv("INCREMENTAL_RUN_INTERVAL_MINUTES", "60")) * 60
    full_interval = timedelta(hours=int(os.getenv("FULL_RESOLUTION_INTERVAL_HOURS", "12")))
    digest_hour = int(os.getenv("DAILY_DIGEST_HOUR", "8"))
    # Recurring status heartbeat to the group chat (hours; 0 disables).
    status_interval_h = int(os.getenv("STATUS_HEARTBEAT_INTERVAL_HOURS", "6"))

    logger.info("Scheduler started: incremental every %d min, full resolution every %s, "
                "digest at %02d:00 UTC, status heartbeat every %dh",
                interval // 60, full_interval, digest_hour, status_interval_h)

    last_digest_date: str | None = None
    last_health_check: datetime | None = None
    last_status: datetime | None = None
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

        # Recurring status heartbeat to the group chat. Fires on the first loop
        # (confirms the system is up) then every status_interval_h hours.
        if status_interval_h > 0 and (
            last_status is None
            or (now - last_status).total_seconds() >= status_interval_h * 3600
        ):
            try:
                await notify_status(await _build_status())
                last_status = now
            except Exception:
                logger.exception("Status heartbeat failed")

        last_full_run = await get_last_run_time("full_resolution")
        if last_full_run is None or (now - last_full_run) >= full_interval:
            logger.info("Starting full resolution (last run: %s)", last_full_run)
            try:
                await run_full_resolution()
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
