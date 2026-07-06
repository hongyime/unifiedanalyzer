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
        # a batch returns processed=0. Progress is printed per batch so the
        # operator can watch a long backfill without inspecting the DB.
        from src.db.connection import init_pools, close_pools
        from src.pipeline.timeline_embedder import embed_new_timeline_events

        async def _run():
            await init_pools()
            try:
                total = 0
                iters = 0
                while True:
                    if args.max_batches is not None and iters >= args.max_batches:
                        logger.info("embed-backfill: reached --max-batches=%d, stopping", args.max_batches)
                        break
                    stats = await embed_new_timeline_events(
                        batch_size=args.batch_size,
                        max_events=args.batch_size,
                    )
                    processed = int(stats.get("processed", 0) or 0)
                    if stats.get("skipped"):
                        logger.warning("embed-backfill: phase skipped: %s", stats)
                        break
                    if processed == 0:
                        logger.info("embed-backfill: drained. total=%d in %d iterations", total, iters)
                        break
                    total += processed
                    iters += 1
                    logger.info("embed-backfill: iter=%d processed=%d cumulative=%d",
                                iters, processed, total)
            finally:
                await close_pools()

        asyncio.run(_run())

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
