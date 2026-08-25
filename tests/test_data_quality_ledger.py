import json
from datetime import datetime, timedelta, timezone

import pytest


class _AnalyzerConn:
    def __init__(self, *, timeline=0, text=0, media=0, indicators=0, exported=0):
        self.values = {
            "timeline": timeline,
            "text": text,
            "media": media,
            "indicators": indicators,
            "exported": exported,
        }
        self.latest = datetime(2026, 8, 21, 5, 0, tzinfo=timezone.utc)

    async def fetch(self, sql, *args):
        if "information_schema.tables" in sql:
            return [
                {"table_name": "timeline_events"},
                {"table_name": "timeline_text_features"},
                {"table_name": "media_analysis"},
                {"table_name": "normalized_indicators"},
            ]
        sources = args[0] if args else ["facebook"]
        source = sources[0]
        if "FROM timeline_events" in sql:
            return [{"source": source, "count": self.values["timeline"], "latest": self.latest}] if self.values["timeline"] else []
        if "FROM timeline_text_features" in sql:
            return [{"source": source, "count": self.values["text"], "latest": self.latest}] if self.values["text"] else []
        if "FROM media_analysis" in sql:
            return [{"source": source, "count": self.values["media"], "latest": self.latest}] if self.values["media"] else []
        if "export_status = 'exported'" in sql:
            return [{"source": source, "count": self.values["exported"], "latest": self.latest}] if self.values["exported"] else []
        if "FROM normalized_indicators" in sql:
            return [{"source": source, "count": self.values["indicators"], "latest": self.latest}] if self.values["indicators"] else []
        raise AssertionError(f"Unexpected analyzer fetch SQL: {sql}")

    async def fetchrow(self, sql, *args):
        if "FROM timeline_events" in sql:
            return {"count": self.values["timeline"], "latest": self.latest}
        if "FROM timeline_text_features" in sql:
            return {"count": self.values["text"], "latest": self.latest}
        if "FROM media_analysis" in sql:
            return {"count": self.values["media"], "latest": self.latest}
        if "export_status = 'exported'" in sql:
            return {"count": self.values["exported"], "latest": self.latest if self.values["exported"] else None}
        if "FROM normalized_indicators" in sql:
            return {"count": self.values["indicators"], "latest": self.latest if self.values["indicators"] else None}
        raise AssertionError(f"Unexpected analyzer SQL: {sql}")


class _FutureAnalyzerConn(_AnalyzerConn):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.latest = datetime(2027, 1, 1, 1, 0, tzinfo=timezone.utc)


class _CollectorConn:
    def __init__(self, *, count=0):
        self.count = count
        self.latest = datetime(2026, 8, 21, 5, 0, tzinfo=timezone.utc)

    async def fetch(self, sql, *args):
        if "information_schema.tables" in sql:
            return [{"table_name": table} for table in args[0]]
        sources = args[0] if args else ["facebook"]
        source = sources[0]
        if "FROM media_items" in sql or "FROM browser_ingest_events" in sql:
            return [{"source": source, "count": self.count, "latest": self.latest}] if self.count else []
        raise AssertionError(f"Unexpected collector fetch SQL: {sql}")

    async def fetchrow(self, sql, *args):
        return {"count": self.count, "latest": self.latest if self.count else None}


def test_cached_data_quality_ledger_returns_fresh_ok_payload(tmp_path, monkeypatch):
    from src.api.routes.data_quality import cached_data_quality_ledger

    path = tmp_path / "ledger.json"
    payload = {
        "status": "ok",
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"gap_sources": 0},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", str(path))
    monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", "900")

    cached = cached_data_quality_ledger()

    assert cached is not None
    assert cached["ok"] is True
    assert cached["cache"]["used"] is True
    assert cached["cache"]["age_seconds"] >= 0


def test_cached_data_quality_ledger_rejects_stale_or_failed_payload(tmp_path, monkeypatch):
    from src.api.routes.data_quality import cached_data_quality_ledger

    path = tmp_path / "ledger.json"
    stale = {
        "status": "ok",
        "ok": True,
        "generated_at": (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),
    }
    path.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", str(path))
    monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", "60")
    assert cached_data_quality_ledger() is None

    failed = {
        "status": "degraded",
        "ok": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(failed), encoding="utf-8")
    assert cached_data_quality_ledger() is None


@pytest.mark.asyncio
async def test_data_quality_ledger_reports_raw_to_analyzer_gap(monkeypatch):
    from src.pipeline.data_quality_ledger import build_data_quality_ledger

    monkeypatch.setenv("ANALYZER_DATA_QUALITY_SOURCES", "facebook")

    result = await build_data_quality_ledger(
        _AnalyzerConn(timeline=0, text=0, media=0, indicators=0, exported=0),
        _CollectorConn(count=5),
    )

    assert result["ok"] is False
    assert result["status"] == "degraded"
    assert result["summary"]["gap_sources"] == 1
    assert result["sources"][0]["state"] == "gap"
    assert "raw evidence" in result["sources"][0]["detail"]


@pytest.mark.asyncio
async def test_data_quality_ledger_reports_indicator_export_gap(monkeypatch):
    from src.pipeline.data_quality_ledger import build_data_quality_ledger

    monkeypatch.setenv("ANALYZER_DATA_QUALITY_SOURCES", "exposure")

    result = await build_data_quality_ledger(
        _AnalyzerConn(timeline=0, text=0, media=0, indicators=3, exported=0),
        _CollectorConn(count=5),
    )

    assert result["ok"] is False
    assert result["sources"][0]["state"] == "export_gap"
    assert result["sources"][0]["analyzer"]["normalized_indicators"]["count"] == 3
    assert result["sources"][0]["analyzer"]["supabase_exported_indicators"]["count"] == 0


@pytest.mark.asyncio
async def test_data_quality_ledger_accepts_analyzer_evidence_path(monkeypatch):
    from src.pipeline.data_quality_ledger import build_data_quality_ledger

    monkeypatch.setenv("ANALYZER_DATA_QUALITY_SOURCES", "x")

    result = await build_data_quality_ledger(
        _AnalyzerConn(timeline=2, text=2, media=1, indicators=1, exported=1),
        _CollectorConn(count=5),
    )

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["sources"][0]["state"] == "ok"
    assert result["sources"][0]["analyzer"]["total_analyzer_signals"] == 6


@pytest.mark.asyncio
async def test_data_quality_ledger_rejects_future_dated_analyzer_evidence(monkeypatch):
    from src.pipeline.data_quality_ledger import build_data_quality_ledger

    monkeypatch.setenv("ANALYZER_DATA_QUALITY_SOURCES", "github")

    result = await build_data_quality_ledger(
        _FutureAnalyzerConn(timeline=2),
        _CollectorConn(count=0),
    )

    assert result["ok"] is False
    assert result["status"] == "degraded"
    assert result["summary"]["gap_sources"] == 1
    assert result["sources"][0]["state"] == "clock_skew"
    assert result["sources"][0]["analyzer"]["latest_age_seconds"] < 0
