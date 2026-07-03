import asyncio
import logging
import os
from datetime import datetime, timezone

from src.db.connection import get_analyzer_pool
from src.pipeline.entity_resolver import resolve_entities
from src.pipeline.timeline_builder import build_timeline
from src.pipeline.alert_engine import run_alerts
from src.pipeline.behavioral_profiler import compute_behavioral_profiles
from src.pipeline.group_graph import build_whatsapp_group_graph, build_telegram_group_graph
from src.pipeline.strava_patterns import analyze_strava_patterns
from src.pipeline.bio_nlp import analyze_bios
from src.pipeline.graph_analytics import compute_graph_analytics
from src.pipeline.graph_overlap import compute_graph_overlap
from src.pipeline.bio_mention import detect_bio_mentions
from src.pipeline.location_inference import infer_locations
from src.pipeline.content_fingerprint import fingerprint_content
from src.pipeline.temporal_correlation import correlate_activity
from src.pipeline.contact_extraction import extract_contacts
from src.pipeline.route_similarity import analyze_route_similarity
from src.pipeline.media_analysis import (
    MEDIA_EXIF_BATCH_SIZE,
    MEDIA_PDF_IMAGE_BATCH_SIZE,
    MEDIA_PDF_TEXT_BATCH_SIZE,
    MEDIA_PHASH_BATCH_SIZE,
    analyze_media_exif,
    analyze_media_pdf_text,
    analyze_media_phash,
    extract_pdf_images,
)
from src.pipeline.media_analysis_tier1 import (
    analyze_media_faces,
    analyze_media_ocr,
    extract_video_frames,
    rebuild_face_match_signals,
)
from src.pipeline.identity_scorer import compute_identity_scores
from src.pipeline.face_clustering import run_face_clustering
from src.pipeline.identity_calibration import maybe_retrain
from src.pipeline.geocode import geocode_step
from src.notifications.alerts import notify_run_summary, notify_error, notify_new_alerts

logger = logging.getLogger(__name__)


# P0-3 (identity_system_review_plan.md): a legitimate run heartbeats every ~60s
# while it executes (see _run_with_heartbeat). The stale-lock cleaner reclaims a
# lock only when the heartbeat (falling back to started_at for pre-heartbeat rows)
# has been silent longer than this, so a genuinely long run (full-res is routinely
# 2-5h) is never reclaimed mid-flight, while a crashed run is freed shortly after
# its heartbeat stops. The old fixed 30-min-since-start timeout reclaimed EVERY
# real full-res's lock, letting an API-triggered run_incremental start concurrently
# and double-write.
#
# CAVEAT: some resolver/media phases are CPU-bound and synchronous (e.g. Phase 4.5
# fuzzy-matches tens of thousands of username-less profiles), which blocks the
# event loop so the heartbeat task cannot fire meanwhile. The threshold must
# therefore exceed the longest single synchronous phase, not just the heartbeat
# interval — hence 30 min, comfortably above observed phase blocks yet far below
# the 2-5h total runtime the old start-based timeout kept tripping on.
_STALE_HEARTBEAT_MINUTES = int(os.getenv("STALE_RUN_HEARTBEAT_MINUTES", "30"))
_HEARTBEAT_INTERVAL_SECONDS = 60


async def _is_run_locked() -> bool:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        cleaned = await conn.fetchval("""
            UPDATE analysis_runs
            SET status = 'failed', finished_at = NOW(),
                error_message = 'Stale lock (heartbeat silent) — cleaned up automatically'
            WHERE status = 'running'
              AND COALESCE(heartbeat_at, started_at) < NOW() - make_interval(mins => $1)
            RETURNING id
        """, _STALE_HEARTBEAT_MINUTES)
        if cleaned:
            logger.warning("Cleaned up stale run lock: %s", cleaned)
        row = await conn.fetchval("""
            SELECT id FROM analysis_runs WHERE status = 'running' LIMIT 1
        """)
    return row is not None


async def _heartbeat_loop(run_id: str) -> None:
    """Bump heartbeat_at every _HEARTBEAT_INTERVAL_SECONDS while a run executes.
    Cancelled by _run_with_heartbeat when the run finishes. Errors are swallowed
    so a transient DB hiccup never kills the run it is guarding."""
    pool = get_analyzer_pool()
    while True:
        try:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE analysis_runs SET heartbeat_at = NOW() WHERE id = $1::uuid",
                    run_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Heartbeat update failed (non-fatal)", exc_info=True)


async def _stop_heartbeat(hb: asyncio.Task) -> None:
    """Cancel a heartbeat task started with asyncio.create_task(_heartbeat_loop(...))
    and await its teardown. Safe to call from a finally block."""
    hb.cancel()
    try:
        await hb
    except asyncio.CancelledError:
        pass


async def clear_orphaned_run_locks() -> int:
    """Mark every still-'running' analysis_runs row as failed. Called once at
    scheduler startup: in the single-scheduler-process model a freshly started
    scheduler is the ONLY scheduler, so any 'running' row was orphaned by a
    predecessor killed mid-run (e.g. a container recreate). Without this, the new
    scheduler would skip runs for up to 30 min until _is_run_locked's stale-lock
    timer fires. Returns the number cleared."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        cleared = await conn.fetch("""
            UPDATE analysis_runs
            SET status = 'failed', finished_at = NOW(),
                error_message = 'Orphaned by scheduler restart — cleared on startup'
            WHERE status = 'running'
            RETURNING id
        """)
    if cleared:
        logger.warning("Cleared %d orphaned run lock(s) on scheduler startup", len(cleared))
    return len(cleared)


async def get_last_run_time(run_type: str) -> datetime | None:
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
            INSERT INTO analysis_runs (run_type, status, heartbeat_at)
            VALUES ($1, 'running', NOW()) RETURNING id::text
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
    heartbeat = asyncio.create_task(_heartbeat_loop(run_id))

    try:
        since = await get_last_run_time("incremental")

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
            await build_telegram_group_graph()
        except Exception:
            logger.exception("Telegram group graph failed (non-fatal)")

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
            await compute_graph_overlap()
        except Exception:
            logger.exception("Graph overlap failed (non-fatal)")

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

        try:
            await extract_contacts()
        except Exception:
            logger.exception("Contact extraction failed (non-fatal)")

        try:
            await analyze_route_similarity()
        except Exception:
            logger.exception("Route similarity analysis failed (non-fatal)")

        # Phase 6: media content analysis (docs/media_analysis_plan.md).
        # Order matters — 6C.2/6H produce derived pdf_image/video_frame rows
        # that 6B/6D/6F also consume via fetch_unprocessed_derived().
        try:
            await analyze_media_pdf_text(limit=MEDIA_PDF_TEXT_BATCH_SIZE)
        except Exception:
            logger.exception("Media PDF text analysis failed (non-fatal)")

        try:
            await extract_pdf_images(limit=MEDIA_PDF_IMAGE_BATCH_SIZE)
        except Exception:
            logger.exception("Media PDF image extraction failed (non-fatal)")

        try:
            await extract_video_frames()
        except Exception:
            logger.exception("Media video frame extraction failed (non-fatal)")

        try:
            await analyze_media_exif(limit=MEDIA_EXIF_BATCH_SIZE)
        except Exception:
            logger.exception("Media EXIF GPS analysis failed (non-fatal)")

        try:
            await analyze_media_phash(limit=MEDIA_PHASH_BATCH_SIZE)
        except Exception:
            logger.exception("Media perceptual hash analysis failed (non-fatal)")

        try:
            await analyze_media_ocr()
        except Exception:
            logger.exception("Media OCR analysis failed (non-fatal)")

        try:
            await analyze_media_faces()
        except Exception:
            logger.exception("Media face analysis failed (non-fatal)")

        # Cluster the InsightFace corpus + propagate entity_faces attribution
        # across clusters (signals-only, group-photo guarded) so the bridged
        # corpus the face-match signal reads from actually has data. Must run
        # BEFORE rebuild_face_match_signals.
        try:
            await run_face_clustering()
        except Exception:
            logger.exception("Face clustering failed (non-fatal)")

        # F3: rebuild the InsightFace-backed media_face_match signal independently
        # of the SFace face step above (which skips when its models are missing).
        try:
            await rebuild_face_match_signals()
        except Exception:
            logger.exception("Face-match signal rebuild failed (non-fatal)")

        # Retrain the calibrated scorer from dashboard-captured labels (entity
        # merges / "not same" dismisses) when enough have accumulated, then score
        # with the fresh model. No-op until ~20 labels exist (noisy-OR until then).
        try:
            await maybe_retrain()
        except Exception:
            logger.exception("Calibration retrain failed (non-fatal)")

        try:
            await compute_identity_scores()
        except Exception:
            logger.exception("Identity scoring failed (non-fatal)")

        # Trickle-geocode Instagram place names for the map (rate-limited, cached).
        try:
            await geocode_step()
        except Exception:
            logger.exception("Geocode step failed (non-fatal)")

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
    finally:
        await _stop_heartbeat(heartbeat)

    return stats


async def hard_reset_entities() -> None:
    """DESTRUCTIVE, manual-only: delete all entities + platform links so the next
    resolution rebuilds the identity graph from scratch with fresh UUIDs.

    Via the schema FKs this CASCADE-deletes entity_faces, identity_signals,
    entity_relationships and behavioral_profiles, NULLs timeline_events.entity_id,
    and orphans identity_labels / entity_views / case_items (which have no FK and
    keep dead UUIDs). This is exactly the damage P0-1 removed from the automatic
    12h full-resolution path — it lives here as an explicit opt-in only. Run it
    solely when you deliberately want to discard the current identity graph, then
    follow with `python -m src.main full` to repopulate."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM entity_platform_links")
        await conn.execute("DELETE FROM entities")
    logger.warning(
        "hard_reset_entities: all entities + platform links deleted (CASCADE wiped "
        "entity_faces/signals/relationships). Run a full resolution to rebuild."
    )


async def run_full_resolution() -> dict:
    if await _is_run_locked():
        logger.warning("Another run is in progress, skipping")
        return {"skipped": True}

    run_id = await _create_run("full_resolution")
    stats = {"entities": 0, "events": 0, "alerts": 0, "signals": 0}
    heartbeat = asyncio.create_task(_heartbeat_loop(run_id))

    try:
        # P0-1 (identity_system_review_plan.md): full resolution re-resolves every
        # entity but MUST NOT delete entities/links first. resolve_entities() is
        # already idempotent and self-cleaning — it UPSERTs entity_platform_links,
        # recovers each entity's existing UUID from those links, handles
        # splits/merges, and deletes orphaned entities at the end. The former
        # `DELETE FROM entity_platform_links; DELETE FROM entities` here emptied the
        # links resolve_entities reads for ID recovery, so EVERY entity got a fresh
        # uuid4() on each ~12h run. Via the schema FKs that CASCADE-wiped
        # entity_faces and orphaned identity_labels / entity_views / case_items
        # (all keyed by entity UUID) twice a day. Keeping IDs stable is what makes
        # bridged faces, human calibration labels, saved cases and review state
        # durable — the precondition for P1-4 (calibration) to accumulate at all.
        #
        # The old `DELETE FROM identity_signals ...` here was additionally dead
        # code: the `DELETE FROM entities` that followed CASCADE-deleted every
        # identity_signals row regardless (FK ON DELETE CASCADE, schema.sql:40).
        #
        # A destructive from-scratch rebuild is now an explicit opt-in
        # (hard_reset_entities below), never the automatic 12h path.
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
            await build_telegram_group_graph()
        except Exception:
            logger.exception("Telegram group graph failed (non-fatal)")

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
            await compute_graph_overlap()
        except Exception:
            logger.exception("Graph overlap failed (non-fatal)")

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

        try:
            await extract_contacts()
        except Exception:
            logger.exception("Contact extraction failed (non-fatal)")

        try:
            await analyze_route_similarity()
        except Exception:
            logger.exception("Route similarity analysis failed (non-fatal)")

        # Phase 6: media content analysis (docs/media_analysis_plan.md).
        # Order matters — 6C.2/6H produce derived pdf_image/video_frame rows
        # that 6B/6D/6F also consume via fetch_unprocessed_derived().
        try:
            await analyze_media_pdf_text(limit=MEDIA_PDF_TEXT_BATCH_SIZE)
        except Exception:
            logger.exception("Media PDF text analysis failed (non-fatal)")

        try:
            await extract_pdf_images(limit=MEDIA_PDF_IMAGE_BATCH_SIZE)
        except Exception:
            logger.exception("Media PDF image extraction failed (non-fatal)")

        try:
            await extract_video_frames()
        except Exception:
            logger.exception("Media video frame extraction failed (non-fatal)")

        try:
            await analyze_media_exif(limit=MEDIA_EXIF_BATCH_SIZE)
        except Exception:
            logger.exception("Media EXIF GPS analysis failed (non-fatal)")

        try:
            await analyze_media_phash(limit=MEDIA_PHASH_BATCH_SIZE)
        except Exception:
            logger.exception("Media perceptual hash analysis failed (non-fatal)")

        try:
            await analyze_media_ocr()
        except Exception:
            logger.exception("Media OCR analysis failed (non-fatal)")

        try:
            await analyze_media_faces()
        except Exception:
            logger.exception("Media face analysis failed (non-fatal)")

        # Cluster the InsightFace corpus + propagate entity_faces attribution
        # across clusters (signals-only, group-photo guarded) so the bridged
        # corpus the face-match signal reads from actually has data. Must run
        # BEFORE rebuild_face_match_signals.
        try:
            await run_face_clustering()
        except Exception:
            logger.exception("Face clustering failed (non-fatal)")

        # F3: rebuild the InsightFace-backed media_face_match signal independently
        # of the SFace face step above (which skips when its models are missing).
        try:
            await rebuild_face_match_signals()
        except Exception:
            logger.exception("Face-match signal rebuild failed (non-fatal)")

        # Retrain the calibrated scorer from dashboard-captured labels (entity
        # merges / "not same" dismisses) when enough have accumulated, then score
        # with the fresh model. No-op until ~20 labels exist (noisy-OR until then).
        try:
            await maybe_retrain()
        except Exception:
            logger.exception("Calibration retrain failed (non-fatal)")

        try:
            await compute_identity_scores()
        except Exception:
            logger.exception("Identity scoring failed (non-fatal)")

        # Trickle-geocode Instagram place names for the map (rate-limited, cached).
        try:
            await geocode_step()
        except Exception:
            logger.exception("Geocode step failed (non-fatal)")

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
    finally:
        await _stop_heartbeat(heartbeat)

    return stats
