import os
import asyncpg
import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_analyzer_pool: asyncpg.Pool | None = None
_collector_pool: asyncpg.Pool | None = None


def _get_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


def _parse_dsn(dsn: str) -> dict:
    parsed = urlparse(dsn)
    host = parsed.hostname or "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    return {
        "host": host,
        "port": parsed.port or 5432,
        "user": parsed.username or "collector",
        "password": parsed.password or "",
        "database": parsed.path.lstrip("/") or "postgres",
    }


async def init_pools() -> None:
    global _analyzer_pool, _collector_pool

    max_size = int(os.getenv("DB_MAX_POOL_SIZE", "10"))

    analyzer_params = _parse_dsn(_get_env("ANALYZER_DATABASE_URL"))
    collector_params = _parse_dsn(_get_env("COLLECTOR_DATABASE_URL"))

    _analyzer_pool = await asyncpg.create_pool(
        **analyzer_params,
        min_size=2,
        max_size=max_size,
        ssl="disable",
        command_timeout=300,
    )
    _collector_pool = await asyncpg.create_pool(
        **collector_params,
        min_size=2,
        max_size=max_size,
        ssl="disable",
        command_timeout=300,
    )

    await apply_schema()
    logger.info("Database pools initialized")


async def apply_schema() -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql)


async def close_pools() -> None:
    global _analyzer_pool, _collector_pool
    if _analyzer_pool:
        await _analyzer_pool.close()
        _analyzer_pool = None
    if _collector_pool:
        await _collector_pool.close()
        _collector_pool = None


def get_analyzer_pool() -> asyncpg.Pool:
    if _analyzer_pool is None:
        raise RuntimeError("Analyzer pool not initialized — call init_pools() first")
    return _analyzer_pool


def get_collector_pool() -> asyncpg.Pool:
    if _collector_pool is None:
        raise RuntimeError("Collector pool not initialized — call init_pools() first")
    return _collector_pool
