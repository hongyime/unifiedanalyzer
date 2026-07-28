import pytest

from src.main import clear_stale_running_locks_before_scheduler_import


class _Conn:
    def __init__(self):
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return [{"id": "run-1"}]


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def acquire(self):
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_scheduler_startup_clears_only_stale_running_locks(monkeypatch):
    monkeypatch.setenv("STALE_RUN_HEARTBEAT_MINUTES", "90")
    pool = _Pool()

    cleared = await clear_stale_running_locks_before_scheduler_import(pool)

    assert cleared == 1
    query, args = pool.conn.calls[0]
    assert "WHERE status = 'running'" in query
    assert "COALESCE(heartbeat_at, started_at)" in query
    assert "make_interval(mins => $1)" in query
    assert "Interrupted by scheduler restart" not in query
    assert args == (90,)
