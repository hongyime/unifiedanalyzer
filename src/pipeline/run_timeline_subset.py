import argparse
import asyncio
import json

from src.db.connection import close_pools, init_pools
from src.pipeline.timeline_builder import build_timeline


def _csv_set(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    out = {part.strip() for part in raw.split(",") if part.strip()}
    return out or None


async def _run(args) -> None:
    await init_pools()
    try:
        stats = await build_timeline(
            since=None,
            skip_sources=_csv_set(args.skip_sources),
            only_sources=_csv_set(args.sources),
            only_event_types=_csv_set(args.event_types),
        )
        print(json.dumps(stats, default=str))
    finally:
        await close_pools()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a filtered timeline backfill.")
    parser.add_argument("--sources", help="Comma-separated sources to include")
    parser.add_argument("--event-types", help="Comma-separated event types to include")
    parser.add_argument("--skip-sources", help="Comma-separated sources to skip")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
