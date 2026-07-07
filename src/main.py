import os
import sys
import asyncio
import logging
import argparse

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("unifiedanalyzer")


def main():
    parser = argparse.ArgumentParser(description="UnifiedAnalyzer — Personal OSINT Analyzer")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Start the FastAPI server (API only; scheduler is separate)")
    sub.add_parser("scheduler", help="Run the analysis scheduler loop (separate process)")
    sub.add_parser("run", help="Run one incremental analysis cycle")
    sub.add_parser("full", help="Run full identity resolution")
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
        from src.db.connection import init_pools, close_pools
        from src.scheduler.scheduler import start_scheduler

        async def _run():
            await init_pools()
            try:
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
