import pytest

from src.pipeline import indicator_export


def _r(t, v):
    return {"indicator_type": t, "normalized_value": v}


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *_a, **_k):
        return self._rows

    async def execute(self, *_a, **_k):
        return "DELETE 0"


@pytest.mark.asyncio
async def test_reconcile_reports_orphans(monkeypatch):
    local = _FakeConn([_r("domain", "a.com"), _r("email", "x@a.com")])
    remote = _FakeConn([_r("domain", "a.com"), _r("email", "x@a.com"), _r("domain", "orphan.com")])
    monkeypatch.setattr(indicator_export, "_supabase_mode", lambda mode=None: "postgres_direct")

    res = await indicator_export.reconcile_supabase_indicators(local, remote_conn=remote)

    assert res["status"] == "ok"
    assert res["local_exported"] == 2
    assert res["remote_rows"] == 3
    assert res["orphans"] == 1
    assert res["deleted"] == 0
    assert res["orphan_samples"][0]["normalized_value"] == "orphan.com"


@pytest.mark.asyncio
async def test_reconcile_clean_deletes_orphans(monkeypatch):
    deleted = []

    class _RemoteDel(_FakeConn):
        async def execute(self, _sql, types, values, *_a, **_k):
            deleted.append((types, values))
            return f"DELETE {len(types)}"

    local = _FakeConn([_r("domain", "a.com")])
    remote = _RemoteDel([_r("domain", "a.com"), _r("domain", "orphan.com")])
    monkeypatch.setattr(indicator_export, "_supabase_mode", lambda mode=None: "postgres_direct")

    res = await indicator_export.reconcile_supabase_indicators(local, clean=True, remote_conn=remote)

    assert res["orphans"] == 1
    assert res["deleted"] == 1
    assert deleted[0][1] == ["orphan.com"]


@pytest.mark.asyncio
async def test_reconcile_skips_when_mode_disabled(monkeypatch):
    monkeypatch.setattr(indicator_export, "_supabase_mode", lambda mode=None: "disabled")
    res = await indicator_export.reconcile_supabase_indicators(_FakeConn([]), remote_conn=_FakeConn([]))
    assert res["status"] == "skipped"
