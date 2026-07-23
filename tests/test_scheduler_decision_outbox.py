import pytest


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_decision_outbox_check_retries_pending_jsonl(monkeypatch):
    from src.scheduler import scheduler

    conn = object()
    calls = []

    async def fake_retry(got_conn, *, limit):
        calls.append((got_conn, limit))
        return {"pending": 2, "already_present": 0, "written": 2, "failed": 0}

    monkeypatch.setenv("ANALYZER_DECISION_OUTBOX_ENABLED", "1")
    monkeypatch.setenv("ANALYZER_DECISION_OUTBOX_LIMIT", "25")
    monkeypatch.setattr(scheduler, "get_analyzer_pool", lambda: _Pool(conn))
    monkeypatch.setattr(scheduler, "retry_pending_decision_jsonl", fake_retry)

    stats = await scheduler._run_decision_outbox_check()

    assert stats["written"] == 2
    assert calls == [(conn, 25)]


@pytest.mark.asyncio
async def test_decision_outbox_check_can_be_disabled(monkeypatch):
    from src.scheduler import scheduler

    monkeypatch.setenv("ANALYZER_DECISION_OUTBOX_ENABLED", "0")

    stats = await scheduler._run_decision_outbox_check()

    assert stats == {"skipped": "disabled"}
