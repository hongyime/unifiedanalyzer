"""
QA-lane DB integration test fixtures.

All fixtures skip automatically when ANALYZER_DATABASE_URL is unset or
the DB is unreachable — so the test suite remains green in CI without a
running Postgres, and picks up real coverage when one IS available.

Usage in tests:
    @pytest.mark.asyncio
    async def test_foo(analyzer_conn):
        row = await analyzer_conn.fetchval("SELECT 1")
        assert row == 1
"""
from __future__ import annotations

import os

import asyncpg
import pytest

_ANALYZER_URL = os.getenv(
    "ANALYZER_DATABASE_URL",
    "postgres://collector:collector@localhost:5500/unifiedanalyzer",
)
_COLLECTOR_URL = os.getenv(
    "COLLECTOR_DATABASE_URL",
    "postgres://collector:collector@localhost:5500/unifiedcollector",
)


async def _try_connect(url: str, timeout: float = 3.0):
    """Return a connection if the DB is reachable, else return None."""
    try:
        conn = await asyncpg.connect(url, timeout=timeout)
        await conn.fetchval("SELECT 1")
        return conn
    except Exception:
        return None


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "db_integration: mark test as requiring a live database (skipped in CI without DB).",
    )


@pytest.fixture(scope="session")
def analyzer_url():
    return _ANALYZER_URL


@pytest.fixture(scope="session")
def collector_url():
    return _COLLECTOR_URL


@pytest.fixture(scope="session")
async def _analyzer_pool_session(anyio_backend):
    """Session-scoped asyncpg pool — skips if DB unreachable."""
    probe = await _try_connect(_ANALYZER_URL)
    if probe is None:
        pytest.skip("Analyzer DB not reachable — skipping DB integration tests")
    await probe.close()
    pool = await asyncpg.create_pool(_ANALYZER_URL, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
async def analyzer_conn(_analyzer_pool_session):
    """Per-test connection from the session pool, auto-closed."""
    async with _analyzer_pool_session.acquire() as conn:
        yield conn


@pytest.fixture(scope="session")
async def _collector_pool_session(anyio_backend):
    """Session-scoped collector pool — skips if DB unreachable."""
    probe = await _try_connect(_COLLECTOR_URL)
    if probe is None:
        pytest.skip("Collector DB not reachable — skipping DB integration tests")
    await probe.close()
    pool = await asyncpg.create_pool(_COLLECTOR_URL, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
async def collector_conn(_collector_pool_session):
    """Per-test connection from the collector session pool, auto-closed."""
    async with _collector_pool_session.acquire() as conn:
        yield conn
