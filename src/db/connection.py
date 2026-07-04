import os
import asyncio
import asyncpg
import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_analyzer_pool: asyncpg.Pool | None = None
_collector_pool: asyncpg.Pool | None = None

RETRY_DELAYS = [5, 10, 20, 40, 60]


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


async def _create_pool_with_retry(params: dict, max_size: int, label: str) -> asyncpg.Pool:
    attempt = 0
    while True:
        try:
            min_size = max(1, max_size // 4)
            pool = await asyncpg.create_pool(
                **params,
                min_size=min_size,
                max_size=max_size,
                ssl="disable",
                command_timeout=300,
            )
            if attempt > 0:
                logger.info("Connected to %s after %d retries", label, attempt)
            return pool
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, ConnectionError) as e:
            if attempt == 0:
                logger.warning("Waiting for %s database (%s). Will retry quietly...", label, e.__class__.__name__)
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            await asyncio.sleep(delay)
            attempt += 1


async def init_pools(apply_schema_ddl: bool = True) -> None:
    global _analyzer_pool, _collector_pool

    max_size = int(os.getenv("DB_MAX_POOL_SIZE", "10"))

    analyzer_params = _parse_dsn(_get_env("ANALYZER_DATABASE_URL"))
    collector_params = _parse_dsn(_get_env("COLLECTOR_DATABASE_URL"))

    _analyzer_pool = await _create_pool_with_retry(analyzer_params, max_size, "analyzer")
    _collector_pool = await _create_pool_with_retry(collector_params, max_size, "collector")

    if apply_schema_ddl:
        # apply_schema()'s DDL (CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS) can
        # block for the full command_timeout if another connection holds a
        # conflicting lock (e.g. the live "serve" process mid-transaction).
        # One-off scripts that only need to read/write existing tables should
        # pass apply_schema_ddl=False to skip this — the schema is applied
        # once by `python -m src.main schema` / on "serve" startup.
        await apply_schema()
    logger.info("Database pools initialized")


async def apply_schema() -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    pool = get_analyzer_pool()
    # Schema DDL can include a large index build (e.g. a unique index on a
    # multi-million-row table) that far exceeds the pool's default command_timeout
    # (300s). Cancelling mid-build rolls it back and — on a restart-loop — never
    # completes, which previously crash-looped the API. Give schema application a
    # generous per-call timeout so heavy CREATE INDEX statements can finish.
    schema_timeout = int(os.getenv("SCHEMA_APPLY_TIMEOUT_SECONDS", "1800"))
    async with pool.acquire() as conn:
        await conn.execute(sql, timeout=schema_timeout)


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


async def check_db_connectivity() -> bool:
    try:
        if _analyzer_pool is None or _collector_pool is None:
            return False
        async with _analyzer_pool.acquire(timeout=5) as conn:
            await conn.fetchval("SELECT 1")
        async with _collector_pool.acquire(timeout=5) as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False
