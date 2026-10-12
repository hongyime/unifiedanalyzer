from __future__ import annotations

import pytest

from src.pipeline import recon_bridge


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _Connection:
    def __init__(self, *, rows=None, cursor=None, cursor_exists=True):
        self.rows = rows or []
        self.saved_cursor = cursor
        self.cursor_exists = cursor_exists
        self.fetch_since = "unset"
        self.cursor_checks = []
        self.state_reads = 0
        self.cursor_writes = []

    async def fetch(self, query, *args):
        assert "FROM recon_observations ro" in query
        self.fetch_since = args[0]
        return self.rows

    async def fetchval(self, query, *args):
        if "FROM pipeline_state" in query:
            self.state_reads += 1
            return self.saved_cursor
        if "SELECT EXISTS" in query:
            self.cursor_checks.append(args[0])
            return self.cursor_exists
        if "FROM entity_platform_links" in query:
            return None
        if "FROM identity_signals" in query:
            return None
        raise AssertionError(f"unexpected fetchval query: {query}")

    async def execute(self, query, *args):
        assert "INSERT INTO pipeline_state" in query
        self.cursor_writes.append(args)
        self.saved_cursor = args[1]


def _observation(obs_id: str) -> dict:
    return {
        "obs_id": obs_id,
        "target_id": "target-id",
        "module": "SpiderFoot test",
        "observation_type": "EMAILADDR",
        "value": "lead@example.com",
        "confidence": 0.8,
        "raw_json": None,
        "target_type": "username",
        "target_value": "unlinked-user",
        "scope_json": None,
    }


@pytest.mark.asyncio
async def test_scheduler_style_tick_loads_and_advances_persisted_cursor(monkeypatch):
    previous = "00000000-0000-0000-0000-000000000005"
    last = "00000000-0000-0000-0000-000000000010"
    collector = _Connection(rows=[_observation(last)])
    analyzer = _Connection(cursor=previous)
    monkeypatch.setattr(recon_bridge, "get_collector_pool", lambda: _Pool(collector))
    monkeypatch.setattr(recon_bridge, "get_analyzer_pool", lambda: _Pool(analyzer))

    summary = await recon_bridge.bridge_recon_observations(dry_run=False, limit=1000)

    assert collector.fetch_since == previous
    assert collector.cursor_checks == [previous]
    assert analyzer.cursor_writes == [("recon_bridge_since_id", last)]
    assert summary["cursor"] == last
    assert summary["orphaned_no_entity"] == 1


@pytest.mark.asyncio
async def test_missing_persisted_cursor_restarts_from_null(monkeypatch):
    missing = "00000000-0000-0000-0000-000000000005"
    last = "00000000-0000-0000-0000-000000000010"
    collector = _Connection(rows=[_observation(last)], cursor_exists=False)
    analyzer = _Connection(cursor=missing)
    monkeypatch.setattr(recon_bridge, "get_collector_pool", lambda: _Pool(collector))
    monkeypatch.setattr(recon_bridge, "get_analyzer_pool", lambda: _Pool(analyzer))

    summary = await recon_bridge.bridge_recon_observations(dry_run=False, limit=10)

    assert collector.fetch_since is None
    assert analyzer.cursor_writes == [("recon_bridge_since_id", last)]
    assert summary["cursor"] == last


@pytest.mark.asyncio
async def test_empty_tick_keeps_saved_cursor(monkeypatch):
    saved = "00000000-0000-0000-0000-000000000005"
    collector = _Connection(cursor_exists=True)
    analyzer = _Connection(cursor=saved)
    monkeypatch.setattr(recon_bridge, "get_collector_pool", lambda: _Pool(collector))
    monkeypatch.setattr(recon_bridge, "get_analyzer_pool", lambda: _Pool(analyzer))

    summary = await recon_bridge.bridge_recon_observations(dry_run=False, limit=10)

    assert summary["input_rows"] == 0
    assert summary["cursor"] == saved
    assert analyzer.cursor_writes == []


@pytest.mark.asyncio
async def test_explicit_since_id_is_ephemeral_and_overrides_saved_cursor(monkeypatch):
    explicit = "00000000-0000-0000-0000-000000000007"
    last = "00000000-0000-0000-0000-000000000010"
    collector = _Connection(rows=[_observation(last)])
    analyzer = _Connection(cursor="00000000-0000-0000-0000-000000000005")
    monkeypatch.setattr(recon_bridge, "get_collector_pool", lambda: _Pool(collector))
    monkeypatch.setattr(recon_bridge, "get_analyzer_pool", lambda: _Pool(analyzer))

    summary = await recon_bridge.bridge_recon_observations(
        dry_run=False,
        limit=10,
        since_id=explicit,
    )

    assert collector.fetch_since == explicit
    assert collector.cursor_checks == []
    assert analyzer.state_reads == 0
    assert analyzer.cursor_writes == []
    assert summary["cursor"] == last


@pytest.mark.asyncio
async def test_failed_signal_insert_does_not_advance_cursor(monkeypatch):
    saved = "00000000-0000-0000-0000-000000000005"
    attempted = "00000000-0000-0000-0000-000000000010"
    collector = _Connection(rows=[_observation(attempted)])
    analyzer = _Connection(cursor=saved)
    monkeypatch.setattr(recon_bridge, "get_collector_pool", lambda: _Pool(collector))
    monkeypatch.setattr(recon_bridge, "get_analyzer_pool", lambda: _Pool(analyzer))
    monkeypatch.setattr(recon_bridge, "_resolve_entity", _return_entity)
    monkeypatch.setattr(recon_bridge, "_already_bridged", _not_bridged)
    monkeypatch.setattr(recon_bridge, "_insert_signal", _fail_insert)

    summary = await recon_bridge.bridge_recon_observations(dry_run=False, limit=10)

    assert summary["insert_failures"] == 1
    assert summary["cursor"] == saved
    assert analyzer.cursor_writes == []


async def _return_entity(_conn, _target_value):
    return "entity-id"


async def _not_bridged(_conn, _obs_id):
    return False


async def _fail_insert(*_args, **_kwargs):
    raise RuntimeError("simulated database insert failure")
