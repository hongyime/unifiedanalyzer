import os
import sys
import asyncio
import logging
import argparse
import json

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("unifiedanalyzer")


def _stale_run_heartbeat_minutes() -> int:
    try:
        return max(1, int(os.getenv("STALE_RUN_HEARTBEAT_MINUTES", "30")))
    except (TypeError, ValueError):
        return 30


async def clear_stale_running_locks_before_scheduler_import(pool) -> int:
    """Free only stale run locks before importing the heavy scheduler pipeline."""
    stale_minutes = _stale_run_heartbeat_minutes()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            UPDATE analysis_runs
            SET status = 'failed', finished_at = NOW(),
                error_message = 'Stale run lock (heartbeat silent) - cleared before scheduler pipeline import'
            WHERE status = 'running'
              AND COALESCE(heartbeat_at, started_at) < NOW() - make_interval(mins => $1)
            RETURNING id::text
        """, stale_minutes)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="UnifiedAnalyzer — Personal OSINT Analyzer")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Start the FastAPI server (API only; scheduler is separate)")
    sub.add_parser("scheduler", help="Run the analysis scheduler loop (separate process)")
    sub.add_parser("run", help="Run one incremental analysis cycle")
    sub.add_parser("full", help="Run full identity resolution")
    backup = sub.add_parser("backup-db", help="Create/list analyzer DB backups")
    backup.add_argument("--kind", choices=["auto", "daily", "weekly", "monthly"],
                        default="daily", help="Backup cadence bucket (default: daily)")
    backup.add_argument("--backup-dir", type=str, default=None,
                        help="Backup root (default: ANALYZER_DB_BACKUP_DIR or Z:/unifiedanalyzer/backups/db)")
    backup.add_argument("--database-url", type=str, default=None,
                        help="Override ANALYZER_DATABASE_URL")
    backup.add_argument("--pg-dump-bin", type=str, default=None,
                        help="pg_dump binary path/name (default: PG_DUMP_BIN or pg_dump)")
    backup.add_argument("--dry-run", action="store_true",
                        help="Show create/prune plan without writing or deleting files")
    backup.add_argument("--list", action="store_true",
                        help="List known backup files as JSON and exit")
    backup.add_argument("--retention-only", action="store_true",
                        help="Only apply retention pruning")
    backup.add_argument("--skip-retention", action="store_true",
                        help="Create the backup without pruning old backups")
    backup.add_argument("--retention-daily", type=int, default=None,
                        help="Daily backups to keep (default: 7)")
    backup.add_argument("--retention-weekly", type=int, default=None,
                        help="Weekly backups to keep (default: 4)")
    backup.add_argument("--retention-monthly", type=int, default=None,
                        help="Monthly backups to keep (default: 3)")
    replay = sub.add_parser("decision-replay", help="Replay coverage for analyzer decision JSONL")
    replay_mode = replay.add_mutually_exclusive_group()
    replay_mode.add_argument("--dry-run", action="store_true",
                             help="Only report replay status (default)")
    replay_mode.add_argument("--apply", action="store_true",
                             help="Restore audit_log rows from decision JSONL; requires a successful backup by default")
    replay.add_argument("--log-dir", type=str, default=None,
                        help="Decision log directory (default: ANALYZER_DECISION_LOG_DIR)")
    replay.add_argument("--backup-dir", type=str, default=None,
                        help="Backup root to inspect for --apply guard when analyzer_backup_runs has no success row")
    replay.add_argument("--backup-max-age-hours", type=int, default=24,
                        help="Maximum age for a backup accepted by --apply (default: 24)")
    replay.add_argument("--allow-no-backup", action="store_true",
                        help="Allow --apply without a backup guard; intended only for scratch restore drills")
    replay.add_argument("--unresolved-only", action="store_true",
                        help="Print only unresolved, ambiguous, and invalid event details")
    replay.add_argument("--fail-on-unresolved", action="store_true",
                        help="Exit non-zero if replay finds unresolved, ambiguous, or invalid events")
    replay.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    replay.add_argument("--limit", type=int, default=None, help="Limit decision events scanned")
    recovery = sub.add_parser(
        "recovery-drill",
        help="Restore latest analyzer backup into a scratch DB and replay decision JSONL",
    )
    recovery.add_argument("--backup-dir", type=str, default=None,
                          help="Backup root (default: ANALYZER_DB_BACKUP_DIR)")
    recovery.add_argument("--backup-path", type=str, default=None,
                          help="Specific backup dump to restore")
    recovery.add_argument("--database-url", type=str, default=None,
                          help="Override ANALYZER_DATABASE_URL")
    recovery.add_argument("--scratch-db", type=str, default=None,
                          help="Scratch DB name (must start with ua_restore_drill_)")
    recovery.add_argument("--pg-restore-bin", type=str, default=None,
                          help="pg_restore binary path/name")
    recovery.add_argument("--decision-log-dir", type=str, default=None,
                          help="Decision JSONL directory (default: ANALYZER_DECISION_LOG_DIR)")
    recovery.add_argument("--decision-limit", type=int, default=None,
                          help="Limit decision events replayed during the drill")
    recovery.add_argument("--keep-scratch", action="store_true",
                          help="Do not drop the scratch DB after the drill")
    recovery.add_argument("--dry-run", action="store_true",
                          help="Select backup and scratch DB only; do not restore")
    recovery.add_argument("--recompute-derived", action="store_true",
                          help="After replay, run a full derived rebuild against the scratch DB")
    recovery.add_argument("--recompute-timeout-seconds", type=int, default=None,
                          help="Timeout for --recompute-derived (default: ANALYZER_RECOVERY_RECOMPUTE_TIMEOUT_SECONDS or 7200)")
    recovery.add_argument("--report-path", type=str, default=None,
                          help="Optional path to write the drill JSON report")
    recovery.add_argument("--skip-restore-item", action="append", default=None,
                          help="Skip a matching derived pg_restore INDEX or TABLE DATA item")
    recovery.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    outbox = sub.add_parser("decision-outbox", help="Retry pending audit_log -> decision JSONL writes")
    outbox.add_argument("--limit", type=int, default=100, help="Maximum pending rows to retry")
    outbox.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    priority_hints = sub.add_parser(
        "priority-hints",
        help="Preview or write analyzer-owned collector priority hints",
    )
    priority_mode = priority_hints.add_mutually_exclusive_group()
    priority_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview hints without writing (default)",
    )
    priority_mode.add_argument(
        "--write",
        action="store_true",
        help="Upsert hints into the analyzer collector_priority_hints table",
    )
    priority_hints.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    hard_reset = sub.add_parser(
        "hard-reset-entities",
        help="DESTRUCTIVE: wipe all entities+links (CASCADE wipes faces/signals) then rebuild",
    )
    hard_reset.add_argument("--yes", action="store_true", help="Skip the confirmation guard")
    sub.add_parser("schema", help="Apply database schema (idempotent)")
    # Axis-1 MVP: drain the timeline_embeddings backlog (6.28M rows on
    # first run). Runs the embedder in a loop until 0 new rows come back,
    # so it can be run alongside the scheduler without wedging it.
    ebf = sub.add_parser("embed-backfill",
                         help="Backfill timeline_embeddings (Axis-1 semantic search)")
    ebf.add_argument("--batch-size", type=int, default=500,
                     help="Per-iteration batch size (default 500)")
    ebf.add_argument("--max-batches", type=int, default=None,
                     help="Optional cap on total batches (default: run until drained)")
    ebf.add_argument("--log-file", type=str, default=None,
                     help="Also append structured logs to this path (rotating, ~50MB)")
    ebf.add_argument("--sleep-between", type=float, default=0.0,
                     help="Seconds to sleep between successful batches (default 0)")
    ebf.add_argument("--fail-backoff", type=float, default=30.0,
                     help="Seconds to sleep after a failed iteration (default 30)")
    ebf.add_argument("--max-consecutive-failures", type=int, default=5,
                     help="Exit after this many failures in a row (default 5)")
    tbf = sub.add_parser(
        "text-features-backfill",
        help="Backfill timeline_text_features canonical text side table",
    )
    tbf.add_argument("--batch-size", type=int, default=500,
                     help="Per-iteration batch size (default 500)")
    tbf.add_argument("--max-batches", type=int, default=None,
                     help="Optional cap on total batches (default: run until drained)")
    tbf.add_argument("--sleep-between", type=float, default=0.0,
                     help="Seconds to sleep between successful batches (default 0)")
    tbf.add_argument("--fail-backoff", type=float, default=10.0,
                     help="Seconds to sleep after a failed iteration (default 10)")
    tbf.add_argument("--max-consecutive-failures", type=int, default=5,
                     help="Exit after this many failures in a row (default 5)")
    sbf = sub.add_parser(
        "sentiment-backfill",
        help="Backfill sentiment/emotion columns on timeline_text_features",
    )
    sbf.add_argument("--batch-size", type=int, default=1000,
                     help="Per-iteration batch size (default 1000)")
    sbf.add_argument("--max-batches", type=int, default=None,
                     help="Optional cap on total batches (default: run until drained)")
    sbf.add_argument("--sleep-between", type=float, default=0.0,
                     help="Seconds to sleep between successful batches (default 0)")
    lbf = sub.add_parser(
        "language-backfill",
        help="Backfill timeline_language_profiles from canonical text",
    )
    lbf.add_argument("--batch-size", type=int, default=500)
    lbf.add_argument("--max-batches", type=int, default=None)
    lbf.add_argument("--source", type=str, default=None)
    lbf.add_argument("--language", type=str, default=None)
    lbf.add_argument("--dry-run", action="store_true")
    lbf.add_argument("--json", action="store_true")
    trbf = sub.add_parser(
        "translation-backfill",
        help="Bounded machine-translation backfill for non-English text",
    )
    trbf.add_argument("--batch-size", type=int, default=100)
    trbf.add_argument("--max-batches", type=int, default=None)
    trbf.add_argument("--source", type=str, default=None)
    trbf.add_argument("--language", type=str, default=None)
    trbf.add_argument("--target-language", type=str, default="en")
    trbf.add_argument("--dry-run", action="store_true")
    trbf.add_argument("--json", action="store_true")
    ev = sub.add_parser("eval", help="Run analyzer evaluation task")
    ev.add_argument("--task", required=True, choices=["search", "identity", "sentiment", "face", "location", "alerts"])
    ev.add_argument("--model-or-rule-version", default="manual")
    ev.add_argument("--dry-run", action="store_true")
    ev.add_argument("--json", action="store_true")
    evs = sub.add_parser("eval-seed", help="Seed built-in evaluation smoke sets")
    evs.add_argument("--seed-path", default=None)
    evs.add_argument("--dry-run", action="store_true")
    evs.add_argument("--json", action="store_true")
    sta = sub.add_parser("stream-alerts", help="Run bounded near-real-time alert polling")
    sta.add_argument("--once", action="store_true", help="Run one polling iteration and exit")
    sta.add_argument("--poll-interval", type=float, default=30.0)
    sta.add_argument("--batch-size", type=int, default=1000)
    sta.add_argument("--term-threshold", type=int, default=10)
    sta.add_argument("--message-threshold", type=int, default=50)
    sta.add_argument("--media-threshold", type=int, default=25)
    sta.add_argument("--notify", dest="notify", action="store_true", default=True)
    sta.add_argument("--no-notify", dest="notify", action="store_false")
    sta.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        port = int(os.getenv("API_PORT", "8001"))
        host = os.getenv("API_HOST", "0.0.0.0")
        uvicorn.run("src.api.app:app", host=host, port=port, reload=False)

    elif args.command == "scheduler":
        # Standalone scheduler process: runs the heavy Phase-6 pipeline off the
        # API's event loop so the dashboard stays responsive during runs. Mirrors
        # the face_worker split. start_scheduler() is a long-lived loop.
        from src.db.connection import init_pools, close_pools, get_analyzer_pool

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                cleared = await clear_stale_running_locks_before_scheduler_import(get_analyzer_pool())
                if cleared:
                    logger.warning(
                        "Cleared %d stale running run lock(s) before scheduler pipeline import "
                        "(heartbeat older than %d min)",
                        cleared,
                        _stale_run_heartbeat_minutes(),
                    )
                from src.scheduler.scheduler import start_scheduler
                await start_scheduler()
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "run":
        from src.db.connection import init_pools, close_pools
        from src.pipeline.incremental_runner import run_incremental

        async def _run():
            await init_pools()
            try:
                stats = await run_incremental()
                logger.info("Run complete: %s", stats)
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "full":
        from src.db.connection import init_pools, close_pools
        from src.pipeline.incremental_runner import run_full_resolution

        async def _run():
            await init_pools()
            try:
                stats = await run_full_resolution()
                logger.info("Full resolution complete: %s", stats)
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "backup-db":
        from src.db.backup import (
            BackupConfig,
            BackupError,
            backups_to_json,
            list_backups,
            prune_backups,
            run_backup_kinds,
            run_due_backups,
        )

        try:
            config = BackupConfig.from_env(
                database_url=args.database_url,
                root=args.backup_dir,
                pg_dump_bin=args.pg_dump_bin,
                retention_daily=args.retention_daily,
                retention_weekly=args.retention_weekly,
                retention_monthly=args.retention_monthly,
                allow_missing_database_url=args.list or args.retention_only,
            )
            if args.list:
                print(backups_to_json(list_backups(config.root)))
            elif args.retention_only:
                stale = prune_backups(config, dry_run=args.dry_run)
                key = "would_delete" if args.dry_run else "deleted"
                print(json.dumps({key: [str(p) for p in stale]}, indent=2))
            elif args.kind == "auto":
                result = run_due_backups(
                    config,
                    dry_run=args.dry_run,
                    apply_retention=not args.skip_retention,
                )
                print(json.dumps(result.to_dict(), indent=2))
            else:
                result = run_backup_kinds(
                    config,
                    (args.kind,),
                    dry_run=args.dry_run,
                    apply_retention=not args.skip_retention,
                )
                print(json.dumps(result.to_dict(), indent=2))
        except BackupError as exc:
            logger.error("Analyzer DB backup failed: %s", exc)
            sys.exit(1)

    elif args.command == "decision-replay":
        from src.db.connection import init_pools, close_pools, get_analyzer_pool
        from src.pipeline.decision_replay import (
            BackupRequiredError,
            apply_decision_replay,
            dry_run_decision_replay,
        )

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                pool = get_analyzer_pool()
                async with pool.acquire() as conn:
                    if args.apply:
                        report = await apply_decision_replay(
                            conn,
                            log_dir=args.log_dir,
                            limit=args.limit,
                            require_backup=not args.allow_no_backup,
                            backup_dir=args.backup_dir,
                            backup_max_age_hours=args.backup_max_age_hours,
                        )
                    else:
                        report = await dry_run_decision_replay(
                            conn,
                            log_dir=args.log_dir,
                            limit=args.limit,
                        )
                if args.json:
                    print(json.dumps(
                        report.to_dict(unresolved_only=args.unresolved_only),
                        indent=2,
                        sort_keys=True,
                        default=str,
                    ))
                else:
                    print(report.to_text(unresolved_only=args.unresolved_only))
                replay_report = report.replay if args.apply else report
                if args.fail_on_unresolved and (
                    replay_report.unresolved or replay_report.ambiguous or replay_report.invalid
                ):
                    sys.exit(3)
            finally:
                await close_pools()

        try:
            asyncio.run(_run())
        except BackupRequiredError as exc:
            logger.error("%s", exc)
            sys.exit(2)

    elif args.command == "recovery-drill":
        from src.pipeline.recovery_drill import (
            RecoveryDrillError,
            config_from_env,
            report_to_json,
            run_recovery_drill,
        )

        async def _run():
            config = config_from_env(
                backup_dir=args.backup_dir,
                backup_path=args.backup_path,
                database_url=args.database_url,
                scratch_database=args.scratch_db,
                pg_restore_bin=args.pg_restore_bin,
                decision_log_dir=args.decision_log_dir,
                decision_limit=args.decision_limit,
                keep_scratch=args.keep_scratch,
                dry_run=args.dry_run,
                recompute_derived=args.recompute_derived,
                recompute_timeout_seconds=args.recompute_timeout_seconds,
                skip_restore_item_patterns=args.skip_restore_item,
            )
            report = await run_recovery_drill(config)
            if args.report_path:
                from pathlib import Path
                report_path = Path(args.report_path)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(report_to_json(report) + "\n", encoding="utf-8")
            if args.json:
                print(report_to_json(report))
            else:
                print(report.to_text())
            if report.error:
                sys.exit(2)

        try:
            asyncio.run(_run())
        except RecoveryDrillError as exc:
            logger.error("Analyzer recovery drill failed: %s", exc)
            sys.exit(2)

    elif args.command == "decision-outbox":
        from src.db.connection import init_pools, close_pools, get_analyzer_pool
        from src.util.audit_log import retry_pending_decision_jsonl

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                pool = get_analyzer_pool()
                async with pool.acquire() as conn:
                    stats = await retry_pending_decision_jsonl(conn, limit=args.limit)
                if args.json:
                    print(json.dumps(stats, indent=2, sort_keys=True))
                else:
                    print(
                        "Decision JSONL outbox: "
                        f"{stats['pending']} pending, "
                        f"{stats['already_present']} already present, "
                        f"{stats['written']} written, "
                        f"{stats['failed']} failed"
                    )
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "priority-hints":
        from src.db.connection import init_pools, close_pools, get_analyzer_pool
        from src.pipeline.collector_priority_hints import export_collector_priority_hints

        async def _run():
            await init_pools(apply_schema_ddl=args.write)
            try:
                pool = get_analyzer_pool()
                async with pool.acquire() as conn:
                    report = await export_collector_priority_hints(conn, write=args.write)
                if args.json:
                    print(json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str))
                else:
                    print(report.to_text())
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "hard-reset-entities":
        if not args.yes:
            logger.error(
                "hard-reset-entities is DESTRUCTIVE (wipes all entities, links, "
                "bridged faces, signals). Re-run with --yes to confirm, then run "
                "`python -m src.main full` to rebuild."
            )
            sys.exit(2)
        from src.db.connection import init_pools, close_pools
        from src.pipeline.incremental_runner import hard_reset_entities, run_full_resolution

        async def _run():
            await init_pools()
            try:
                await hard_reset_entities()
                stats = await run_full_resolution()
                logger.info("Hard reset + full resolution complete: %s", stats)
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "schema":
        from src.db.connection import init_pools, close_pools

        async def _run():
            await init_pools()
            logger.info("Schema applied successfully")
            await close_pools()

        asyncio.run(_run())

    elif args.command == "text-features-backfill":
        import time
        import signal
        import traceback
        from src.db.connection import init_pools, close_pools
        from src.pipeline.timeline_text_features import build_timeline_text_features

        stop_flag = {"stop": False}

        def _handle_sig(signum, frame):
            logger.warning("text-features-backfill: received signal %d, will exit after current batch", signum)
            stop_flag["stop"] = True

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handle_sig)
            except (OSError, ValueError):
                pass

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                total = 0
                iters = 0
                fail_streak = 0
                start = time.monotonic()
                logger.info(
                    "text-features-backfill: starting batch_size=%d max_batches=%s",
                    args.batch_size, args.max_batches)
                while not stop_flag["stop"]:
                    if args.max_batches is not None and iters >= args.max_batches:
                        logger.info("text-features-backfill: reached --max-batches=%d, stopping", args.max_batches)
                        break
                    t0 = time.monotonic()
                    try:
                        stats = await build_timeline_text_features(
                            batch_size=args.batch_size,
                            max_events=args.batch_size,
                        )
                        elapsed = time.monotonic() - t0
                        processed = int(stats.get("processed", 0) or 0)
                        written = int(stats.get("inserted", 0) or 0)
                        if processed == 0:
                            logger.info(
                                "text-features-backfill: DRAINED. total=%d iters=%d wall=%.1fs",
                                total, iters, time.monotonic() - start)
                            break
                        total += written
                        iters += 1
                        fail_streak = 0
                        logger.info(
                            "text-features-backfill: iter=%d scanned=%d written=%d cumulative_written=%d batch_wall=%.1fs",
                            iters, processed, written, total, elapsed)
                        if args.sleep_between > 0:
                            await asyncio.sleep(args.sleep_between)
                        continue
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        fail_streak += 1
                        elapsed = time.monotonic() - t0
                        logger.error(
                            "text-features-backfill: iter FAILED after %.1fs (streak=%d) %s: %s\n%s",
                            elapsed, fail_streak, type(e).__name__, e,
                            traceback.format_exc())
                    if fail_streak >= args.max_consecutive_failures:
                        logger.error(
                            "text-features-backfill: %d consecutive failures, giving up. total=%d iters=%d wall=%.1fs",
                            fail_streak, total, iters, time.monotonic() - start)
                        break
                    if fail_streak > 0:
                        await asyncio.sleep(args.fail_backoff)
                logger.info(
                    "text-features-backfill: exit. total_written=%d iters=%d wall=%.1fs stop_flag=%s",
                    total, iters, time.monotonic() - start, stop_flag["stop"])
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "sentiment-backfill":
        import time
        from src.db.connection import init_pools, close_pools
        from src.pipeline.sentiment_emotion import enrich_timeline_sentiment

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                total = 0
                iters = 0
                start = time.monotonic()
                while args.max_batches is None or iters < args.max_batches:
                    stats = await enrich_timeline_sentiment(
                        batch_size=args.batch_size,
                        max_events=args.batch_size,
                    )
                    processed = int(stats.get("processed", 0) or 0)
                    updated = int(stats.get("updated", 0) or 0)
                    if processed == 0:
                        break
                    total += updated
                    iters += 1
                    logger.info("sentiment-backfill: iter=%d processed=%d updated=%d total=%d",
                                iters, processed, updated, total)
                    if args.sleep_between > 0:
                        await asyncio.sleep(args.sleep_between)
                logger.info("sentiment-backfill: exit total_updated=%d iters=%d wall=%.1fs",
                            total, iters, time.monotonic() - start)
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "language-backfill":
        from src.db.connection import init_pools, close_pools
        from src.pipeline.language_id import backfill_language_profiles

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                totals = {"processed": 0, "written": 0, "skipped_non_linguistic": 0, "by_language": {}}
                iters = 0
                while args.max_batches is None or iters < args.max_batches:
                    stats = await backfill_language_profiles(
                        batch_size=args.batch_size,
                        max_events=args.batch_size,
                        source=args.source,
                        language=args.language,
                        dry_run=args.dry_run,
                    )
                    if int(stats.get("processed", 0) or 0) == 0:
                        break
                    totals["processed"] += int(stats.get("processed", 0) or 0)
                    totals["written"] += int(stats.get("written", 0) or 0)
                    totals["skipped_non_linguistic"] += int(stats.get("skipped_non_linguistic", 0) or 0)
                    for lang, count in (stats.get("by_language") or {}).items():
                        totals["by_language"][lang] = totals["by_language"].get(lang, 0) + int(count)
                    iters += 1
                    if args.dry_run:
                        break
                totals["batches"] = iters
                if args.json:
                    print(json.dumps(totals, indent=2, sort_keys=True))
                else:
                    logger.info("language-backfill: %s", totals)
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "translation-backfill":
        from src.db.connection import init_pools, close_pools
        from src.pipeline.translation_worker import run_translation_backfill

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                totals = {"processed": 0, "written": 0, "translated": 0, "skipped": 0, "failed": 0, "by_status": {}}
                iters = 0
                while args.max_batches is None or iters < args.max_batches:
                    stats = await run_translation_backfill(
                        batch_size=args.batch_size,
                        max_events=args.batch_size,
                        target_language=args.target_language,
                        source=args.source,
                        language=args.language,
                        dry_run=args.dry_run,
                    )
                    if int(stats.get("processed", 0) or 0) == 0:
                        break
                    for key in ("processed", "written", "translated", "skipped", "failed"):
                        totals[key] += int(stats.get(key, 0) or 0)
                    for status, count in (stats.get("by_status") or {}).items():
                        totals["by_status"][status] = totals["by_status"].get(status, 0) + int(count)
                    iters += 1
                    if args.dry_run:
                        break
                totals["batches"] = iters
                if args.json:
                    print(json.dumps(totals, indent=2, sort_keys=True))
                else:
                    logger.info("translation-backfill: %s", totals)
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "eval":
        from src.db.connection import init_pools, close_pools, get_analyzer_pool
        from src.eval.runner import run_eval

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                async with get_analyzer_pool().acquire() as conn:
                    report = await run_eval(
                        conn,
                        task=args.task,
                        model_or_rule_version=args.model_or_rule_version,
                        dry_run=args.dry_run,
                    )
                if args.json:
                    print(json.dumps(report, indent=2, sort_keys=True, default=str))
                else:
                    logger.info("eval: %s", report)
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "eval-seed":
        from src.db.connection import init_pools, close_pools, get_analyzer_pool
        from src.eval.runner import seed_eval_sets

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                async with get_analyzer_pool().acquire() as conn:
                    report = await seed_eval_sets(
                        conn,
                        seed_path=args.seed_path,
                        dry_run=args.dry_run,
                    )
                if args.json:
                    print(json.dumps(report, indent=2, sort_keys=True, default=str))
                else:
                    logger.info("eval-seed: %s", report)
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "stream-alerts":
        from src.db.connection import init_pools, close_pools, get_analyzer_pool
        from src.pipeline.stream_alerts import run_stream_alert_once, send_pending_stream_alert_notifications

        async def _run():
            await init_pools(apply_schema_ddl=False)
            try:
                while True:
                    async with get_analyzer_pool().acquire() as conn:
                        report = await run_stream_alert_once(
                            conn,
                            batch_size=args.batch_size,
                            term_threshold=args.term_threshold,
                            message_threshold=args.message_threshold,
                            media_threshold=args.media_threshold,
                        )
                        if args.notify:
                            report["notifications"] = await send_pending_stream_alert_notifications(conn)
                    if args.json:
                        print(json.dumps(report, indent=2, sort_keys=True, default=str))
                    else:
                        logger.info("stream-alerts: %s", report)
                    if args.once:
                        break
                    await asyncio.sleep(args.poll_interval)
            finally:
                await close_pools()

        asyncio.run(_run())

    elif args.command == "embed-backfill":
        # Axis-1 MVP: drive timeline_embeddings from 0 -> 6.28M. Loop until
        # a batch returns processed=0. Per-batch progress + memory + pool
        # telemetry is logged so a stall leaves a clear signature. Per-iter
        # try/except means a bad batch backs off + retries rather than
        # killing the whole loop (which used to happen silently and drop
        # progress on the floor).
        import time
        import signal
        import traceback
        from src.db.connection import init_pools, close_pools, get_analyzer_pool
        from src.pipeline.timeline_embedder import embed_new_timeline_events

        # Optional rotating file logger for long-running backfills. When set,
        # this becomes the SOLE handler on the root logger (basicConfig's
        # default StreamHandler is removed) so each log line writes exactly
        # once. Under the docker/embed-backfill-loop.sh wrapper, python's
        # stdout is redirected to a sidecar .stdout file, and this file
        # handler owns the primary structured log.
        if args.log_file:
            try:
                from logging.handlers import RotatingFileHandler
                fh = RotatingFileHandler(args.log_file, maxBytes=50 * 1024 * 1024,
                                         backupCount=3, encoding="utf-8")
                fh.setFormatter(logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
                root = logging.getLogger()
                # Drop basicConfig's StreamHandler so we don't emit each record
                # twice (once to stdout that the wrapper appends, once here).
                for h in list(root.handlers):
                    if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
                        root.removeHandler(h)
                root.addHandler(fh)
                logger.info("embed-backfill: file logger attached at %s (sole handler)",
                            args.log_file)
            except Exception:
                logger.exception("embed-backfill: could not attach file logger; continuing on stdout")

        # SIGTERM/SIGINT: set a flag; the loop exits at the next iteration
        # boundary. Prevents mid-batch state loss (an interrupted commit
        # would rollback the batch cleanly).
        stop_flag = {"stop": False}
        def _handle_sig(signum, frame):
            logger.warning("embed-backfill: received signal %d, will exit after current batch", signum)
            stop_flag["stop"] = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handle_sig)
            except (OSError, ValueError):
                pass  # non-main thread or platform-unsupported

        def _mem_mb() -> float:
            try:
                import resource
                # ru_maxrss on Linux is KB, on macOS is bytes. Container -> Linux.
                return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
            except Exception:
                return -1.0

        async def _pool_state() -> str:
            try:
                pool = get_analyzer_pool()
                return f"size={pool.get_size()} idle={pool.get_idle_size()} max={pool.get_max_size()}"
            except Exception:
                return "unknown"

        async def _run():
            await init_pools()
            try:
                total = 0
                iters = 0
                fail_streak = 0
                start = time.monotonic()
                logger.info(
                    "embed-backfill: starting batch_size=%d max_batches=%s sleep=%.1fs "
                    "fail_backoff=%.1fs max_consec_fail=%d",
                    args.batch_size, args.max_batches, args.sleep_between,
                    args.fail_backoff, args.max_consecutive_failures)
                while not stop_flag["stop"]:
                    if args.max_batches is not None and iters >= args.max_batches:
                        logger.info("embed-backfill: reached --max-batches=%d, stopping", args.max_batches)
                        break
                    t0 = time.monotonic()
                    try:
                        stats = await embed_new_timeline_events(
                            batch_size=args.batch_size,
                            max_events=args.batch_size,
                        )
                        elapsed = time.monotonic() - t0
                        pool_state = await _pool_state()
                        if stats.get("skipped"):
                            logger.warning(
                                "embed-backfill: phase skipped: %s (mem=%.0fMB pool=%s)",
                                stats, _mem_mb(), pool_state)
                            fail_streak += 1
                        else:
                            processed = int(stats.get("processed", 0) or 0)
                            if processed == 0:
                                logger.info(
                                    "embed-backfill: DRAINED. total=%d iters=%d "
                                    "wall=%.1fs mem=%.0fMB pool=%s",
                                    total, iters, time.monotonic() - start,
                                    _mem_mb(), pool_state)
                                break
                            total += processed
                            iters += 1
                            fail_streak = 0
                            rate = processed / max(elapsed, 0.001)
                            logger.info(
                                "embed-backfill: iter=%d processed=%d cumulative=%d "
                                "batch_wall=%.1fs rate=%.1fev/s mem=%.0fMB pool=%s",
                                iters, processed, total, elapsed, rate,
                                _mem_mb(), pool_state)
                            if args.sleep_between > 0:
                                await asyncio.sleep(args.sleep_between)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        fail_streak += 1
                        elapsed = time.monotonic() - t0
                        logger.error(
                            "embed-backfill: iter FAILED after %.1fs (streak=%d) %s: %s\n%s",
                            elapsed, fail_streak, type(e).__name__, e,
                            traceback.format_exc())
                    if fail_streak >= args.max_consecutive_failures:
                        logger.error(
                            "embed-backfill: %d consecutive failures, giving up. "
                            "total=%d iters=%d wall=%.1fs",
                            fail_streak, total, iters, time.monotonic() - start)
                        break
                    if fail_streak > 0:
                        logger.info("embed-backfill: backing off %.1fs before retry", args.fail_backoff)
                        await asyncio.sleep(args.fail_backoff)
                logger.info(
                    "embed-backfill: exit. total_processed=%d iters=%d wall=%.1fs stop_flag=%s",
                    total, iters, time.monotonic() - start, stop_flag["stop"])
            finally:
                await close_pools()

        asyncio.run(_run())

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
