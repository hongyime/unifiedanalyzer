import logging
from datetime import datetime, timezone

from src.db.connection import get_analyzer_pool
from src.pipeline.entity_resolver import resolve_entities
from src.pipeline.timeline_builder import build_timeline
from src.pipeline.alert_engine import run_alerts
from src.pipeline.behavioral_profiler import compute_behavioral_profiles
from src.pipeline.group_graph import build_whatsapp_group_graph
from src.pipeline.strava_patterns import analyze_strava_patterns
from src.pipeline.bio_nlp import analyze_bios
from src.pipeline.graph_analytics import compute_graph_analytics
from src.pipeline.bio_mention import detect_bio_mentions
from src.pipeline.location_inference import infer_locations
from src.pipeline.content_fingerprint import fingerprint_content
from src.pipeline.temporal_correlation import correlate_activity
from src.notifications.alerts import notify_run_summary, notify_error, notify_new_alerts

logger = logging.getLogger(__name__)


async def _is_run_locked() -> bool:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        cleaned = await conn.fetchval("""
            UPDATE analysis_runs
            SET status = 'failed', finished_at = NOW(),
                error_message = 'Stale lock — cleaned up automatically'
            WHERE status = 'running'
              AND started_at < NOW() - INTERVAL '30 minutes'
            RETURNING id
        """)
        if cleaned:
            logger.warning("Cleaned up stale run lock: %s", cleaned)
        row = await conn.fetchval("""
            SELECT id FROM analysis_runs WHERE status = 'running' LIMIT 1
        """)
    return row is not None


async def _get_last_run_time(run_type: str) -> datetime | None:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval("""
            SELECT finished_at FROM analysis_runs
            WHERE run_type = $1 AND status = 'completed'
            ORDER BY finished_at DESC LIMIT 1
        """, run_type)
    return row


async def _create_run(run_type: str) -> str:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("""
            INSERT INTO analysis_runs (run_type, status) VALUES ($1, 'running') RETURNING id::text
        """, run_type)


async def _get_recent_alerts(since_minutes: int = 5) -> list[dict]:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT alert_type, severity, title
            FROM alerts
            WHERE detected_at > NOW() - INTERVAL '%s minutes'
            ORDER BY detected_at DESC
        """ % since_minutes)
    return [dict(r) for r in rows]


async def _finish_run(run_id: str, stats: dict, error: str | None = None) -> None:
    pool = get_analyzer_pool()
    status = "failed" if error else "completed"
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE analysis_runs SET
                status = $1,
                finished_at = NOW(),
                entities_processed = $2,
                events_created = $3,
                alerts_created = $4,
                signals_created = $5,
                error_message = $6
            WHERE id = $7::uuid
        """, status,
            stats.get("entities", 0),
            stats.get("events", 0),
            stats.get("alerts", 0),
            stats.get("signals", 0),
            error,
            run_id)


async def run_incremental() -> dict:
    if await _is_run_locked():
        logger.warning("Another run is in progress, skipping")
        return {"skipped": True}

    run_id = await _create_run("incremental")
    stats = {"entities": 0, "events": 0, "alerts": 0, "signals": 0}

    try:
        since = await _get_last_run_time("incremental")

        resolver_stats = await resolve_entities()
        stats["entities"] = resolver_stats.get("entities", 0)
        stats["signals"] = resolver_stats.get("signals", 0)

        timeline_stats = await build_timeline(since=since)
        stats["events"] = timeline_stats.get("inserted", 0)

        alert_stats = await run_alerts()
        stats["alerts"] = sum(alert_stats.values())

        await compute_behavioral_profiles()

        try:
            await build_whatsapp_group_graph()
        except Exception:
            logger.exception("WhatsApp group graph failed (non-fatal)")

        try:
            await analyze_strava_patterns()
        except Exception:
            logger.exception("Strava pattern analysis failed (non-fatal)")

        try:
            await analyze_bios()
        except Exception:
            logger.exception("Bio NLP analysis failed (non-fatal)")

        try:
            await compute_graph_analytics()
        except Exception:
            logger.exception("Graph analytics failed (non-fatal)")

        try:
            await detect_bio_mentions()
        except Exception:
            logger.exception("Bio mention detection failed (non-fatal)")

        try:
            await infer_locations()
        except Exception:
            logger.exception("Location inference failed (non-fatal)")

        try:
            await fingerprint_content()
        except Exception:
            logger.exception("Content fingerprint failed (non-fatal)")

        try:
            await correlate_activity()
        except Exception:
            logger.exception("Temporal correlation failed (non-fatal)")

        await _finish_run(run_id, stats)
        logger.info("Incremental run complete: %s", stats)
        await notify_run_summary("incremental", stats)

        if stats["alerts"] > 0:
            new_alerts = await _get_recent_alerts()
            await notify_new_alerts(new_alerts)

    except Exception as e:
        logger.exception("Incremental run failed")
        await _finish_run(run_id, stats, error=str(e))
        await notify_error("incremental", str(e))
        raise

    return stats


async def run_full_resolution() -> dict:
    if await _is_run_locked():
        logger.warning("Another run is in progress, skipping")
        return {"skipped": True}

    run_id = await _create_run("full_resolution")
    stats = {"entities": 0, "events": 0, "alerts": 0, "signals": 0}

    try:
        # Full resolution: clear and rebuild entities
        # Preserve bio_mention signals — they are rebuilt by detect_bio_mentions() below
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM identity_signals WHERE signal_type NOT IN ('bio_mention', 'content_similarity', 'temporal_copost')")
            await conn.execute("DELETE FROM entity_platform_links")
            await conn.execute("DELETE FROM entities")

        resolver_stats = await resolve_entities()
        stats["entities"] = resolver_stats.get("entities", 0)
        stats["signals"] = resolver_stats.get("signals", 0)

        # Rebuild timeline with no since filter
        timeline_stats = await build_timeline(since=None)
        stats["events"] = timeline_stats.get("inserted", 0)

        alert_stats = await run_alerts()
        stats["alerts"] = sum(alert_stats.values())

        await compute_behavioral_profiles()

        try:
            await build_whatsapp_group_graph()
        except Exception:
            logger.exception("WhatsApp group graph failed (non-fatal)")

        try:
            await analyze_strava_patterns()
        except Exception:
            logger.exception("Strava pattern analysis failed (non-fatal)")

        try:
            await analyze_bios()
        except Exception:
            logger.exception("Bio NLP analysis failed (non-fatal)")

        try:
            await compute_graph_analytics()
        except Exception:
            logger.exception("Graph analytics failed (non-fatal)")

        try:
            await detect_bio_mentions()
        except Exception:
            logger.exception("Bio mention detection failed (non-fatal)")

        try:
            await infer_locations()
        except Exception:
            logger.exception("Location inference failed (non-fatal)")

        try:
            await fingerprint_content()
        except Exception:
            logger.exception("Content fingerprint failed (non-fatal)")

        try:
            await correlate_activity()
        except Exception:
            logger.exception("Temporal correlation failed (non-fatal)")

        await _finish_run(run_id, stats)
        logger.info("Full resolution run complete: %s", stats)
        await notify_run_summary("full resolution", stats)

        if stats["alerts"] > 0:
            new_alerts = await _get_recent_alerts()
            await notify_new_alerts(new_alerts)

    except Exception as e:
        logger.exception("Full resolution run failed")
        await _finish_run(run_id, stats, error=str(e))
        await notify_error("full resolution", str(e))
        raise

    return stats
