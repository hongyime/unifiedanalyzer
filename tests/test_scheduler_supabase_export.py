import pytest


@pytest.mark.asyncio
async def test_supabase_scheduler_export_drains_multiple_batches(monkeypatch):
    from src.scheduler import scheduler

    calls = []
    results = [
        {"status": "ok", "mode": "postgres_direct", "write_method": "postgres_direct", "payload": "normalized_indicators_only", "selected": 100, "exported": 100},
        {"status": "ok", "mode": "postgres_direct", "write_method": "postgres_direct", "payload": "normalized_indicators_only", "selected": 100, "exported": 100},
        {"status": "ok", "mode": "postgres_direct", "write_method": "postgres_direct", "payload": "normalized_indicators_only", "selected": 25, "exported": 25},
    ]

    async def fake_export(conn, **kwargs):
        calls.append((conn, kwargs))
        return results[len(calls) - 1]

    monkeypatch.setenv("ANALYZER_SUPABASE_EXPORT_BATCH_SIZE", "100")
    monkeypatch.setenv("ANALYZER_SUPABASE_EXPORT_MAX_BATCHES_PER_PASS", "10")
    monkeypatch.setattr(scheduler, "export_pending_supabase_indicators", fake_export)

    summary = await scheduler._export_supabase_indicators_until_drained("local-conn")

    assert summary["drained"] is True
    assert summary["batches"] == 3
    assert summary["selected"] == 225
    assert summary["exported"] == 225
    assert calls[0][1]["ensure_schema"] is True
    assert calls[0][1]["ensure_schema_when_empty"] is True
    assert calls[1][1]["ensure_schema"] is False


@pytest.mark.asyncio
async def test_supabase_scheduler_export_respects_max_batches(monkeypatch):
    from src.scheduler import scheduler

    calls = []

    async def fake_export(conn, **kwargs):
        calls.append((conn, kwargs))
        return {
            "status": "ok",
            "mode": "postgres_direct",
            "write_method": "postgres_direct",
            "payload": "normalized_indicators_only",
            "selected": 100,
            "exported": 100,
        }

    monkeypatch.setenv("ANALYZER_SUPABASE_EXPORT_BATCH_SIZE", "100")
    monkeypatch.setenv("ANALYZER_SUPABASE_EXPORT_MAX_BATCHES_PER_PASS", "2")
    monkeypatch.setattr(scheduler, "export_pending_supabase_indicators", fake_export)

    summary = await scheduler._export_supabase_indicators_until_drained("local-conn")

    assert summary["drained"] is False
    assert summary["batches"] == 2
    assert summary["selected"] == 200
    assert summary["exported"] == 200
    assert len(calls) == 2
