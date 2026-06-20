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
    sub.add_parser("schema", help="Apply database schema (idempotent)")

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

    elif args.command == "schema":
        from src.db.connection import init_pools, close_pools

        async def _run():
            await init_pools()
            logger.info("Schema applied successfully")
            await close_pools()

        asyncio.run(_run())

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
