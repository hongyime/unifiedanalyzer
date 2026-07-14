import argparse
import asyncio
import json

from src.db.connection import close_pools, init_pools
from src.pipeline.interaction_graph import build_interaction_graph


def _csv_set(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    out = {part.strip() for part in raw.split(",") if part.strip()}
    return out or None


async def _run(args) -> None:
    await init_pools(apply_schema_ddl=False)
    try:
        stats = await build_interaction_graph(
            only_sources=_csv_set(args.sources),
            only_types=_csv_set(args.interaction_types),
        )
        print(json.dumps(stats, default=str))
    finally:
        await close_pools()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a filtered interaction backfill.")
    parser.add_argument("--sources", help="Comma-separated sources to include")
    parser.add_argument("--interaction-types", help="Comma-separated interaction types to include")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
