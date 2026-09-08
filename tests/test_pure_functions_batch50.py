"""
Pure-function tests — batch 50.

Covers previously untested pure functions:
- api.routes.collector_health: _collector_from_live_row, _collectors_from_live
- api.routes.data_quality: pure helper functions
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collector_from_live_row, _collectors_from_live
# ---------------------------------------------------------------------------

class TestCollectorFromLiveRow:
    def _make_row(self, **kw):
        defaults = {
            "source": "telegram",
            "status": "active",
            "collection_mode": "continuous",
            "source_health_last_success_at": "2026-01-15T12:00:00Z",
            "source_health_updated_at": None,
            "browser_heartbeat_at": None,
            "bridge_status": None,
            "bridge_detail": None,
            "detail": None,
        }
        defaults.update(kw)
        return defaults

    def _c(self, row, targets=None):
        from src.api.routes.collector_health import _collector_from_live_row
        return _collector_from_live_row(row, targets or [])

    def test_source_stored(self):
        result = self._c(self._make_row())
        assert result["source"] == "telegram"

    def test_items_24h_zero(self):
        result = self._c(self._make_row())
        assert result["items_24h"] == 0

    def test_last_completed_from_last_success(self):
        result = self._c(self._make_row())
        assert result["last_completed"] is not None
        assert "2026-01-15" in result["last_completed"]

    def test_required_keys_present(self):
        result = self._c(self._make_row())
        for key in ("source", "status", "items_24h", "last_completed",
                    "blocker", "targets"):
            assert key in result

    def test_blocker_uses_bridge_status(self):
        row = self._make_row(bridge_status="error", bridge_detail="auth failed")
        result = self._c(row)
        assert result["blocker"]["kind"] == "error"

    def test_targets_stored(self):
        targets = [{"status": "active", "count": 10}]
        result = self._c(self._make_row(), targets=targets)
        assert result["targets"] == targets


class TestCollectorsFromLive:
    def _c(self, live, targets=None):
        from src.api.routes.collector_health import _collectors_from_live
        return _collectors_from_live(live, targets or [])

    def test_empty_live_returns_empty(self):
        assert self._c({}) == []

    def test_empty_sources_returns_empty(self):
        assert self._c({"sources": []}) == []

    def test_parses_source_rows(self):
        live = {
            "sources": [
                {"source": "instagram", "status": "active",
                 "source_health_last_success_at": None,
                 "source_health_updated_at": None,
                 "browser_heartbeat_at": None,
                 "collection_mode": "continuous",
                 "bridge_status": None, "bridge_detail": None, "detail": None},
            ]
        }
        result = self._c(live)
        assert len(result) == 1
        assert result[0]["source"] == "instagram"

    def test_sorted_by_source(self):
        live = {
            "sources": [
                {"source": "z_source", "status": "active",
                 "source_health_last_success_at": None,
                 "source_health_updated_at": None,
                 "browser_heartbeat_at": None,
                 "collection_mode": "continuous",
                 "bridge_status": None, "bridge_detail": None, "detail": None},
                {"source": "a_source", "status": "active",
                 "source_health_last_success_at": None,
                 "source_health_updated_at": None,
                 "browser_heartbeat_at": None,
                 "collection_mode": "continuous",
                 "bridge_status": None, "bridge_detail": None, "detail": None},
            ]
        }
        result = self._c(live)
        assert result[0]["source"] == "a_source"

    def test_skips_rows_without_source(self):
        live = {
            "sources": [
                {"status": "active"},
                {"source": "telegram", "status": "active",
                 "source_health_last_success_at": None,
                 "source_health_updated_at": None,
                 "browser_heartbeat_at": None,
                 "collection_mode": "continuous",
                 "bridge_status": None, "bridge_detail": None, "detail": None},
            ]
        }
        result = self._c(live)
        assert len(result) == 1
        assert result[0]["source"] == "telegram"


# ---------------------------------------------------------------------------
# api.routes.data_quality: _cache_path, _cache_ttl_seconds (already tested
# in batch2), adding _write_ledger_cache (no-op on ok=False) + cached_data_quality_ledger
# ---------------------------------------------------------------------------

class TestWriteLedgerCache:
    def test_does_not_write_when_ok_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", str(tmp_path / "cache.json"))
        from src.api.routes.data_quality import _write_ledger_cache
        # ok=False → should not write
        _write_ledger_cache({"ok": False, "generated_at": "2026-01-01"})
        assert not (tmp_path / "cache.json").exists()

    def test_does_not_write_when_ok_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", str(tmp_path / "cache.json"))
        from src.api.routes.data_quality import _write_ledger_cache
        _write_ledger_cache({"generated_at": "2026-01-01"})
        assert not (tmp_path / "cache.json").exists()

    def test_writes_when_ok_true(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", str(tmp_path / "cache.json"))
        from src.api.routes.data_quality import _write_ledger_cache
        payload = {"ok": True, "generated_at": "2026-01-01T00:00:00Z", "data": {}}
        _write_ledger_cache(payload)
        cache_file = tmp_path / "cache.json"
        assert cache_file.exists()
        written = json.loads(cache_file.read_text())
        assert written["ok"] is True


class TestCachedDataQualityLedger:
    def test_returns_none_when_no_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", str(tmp_path / "nonexistent.json"))
        from src.api.routes.data_quality import cached_data_quality_ledger
        assert cached_data_quality_ledger() is None

    def test_returns_payload_when_fresh_cache(self, tmp_path, monkeypatch):
        import json
        from datetime import datetime, timezone
        cache_path = tmp_path / "cache.json"
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", str(cache_path))
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", "900")
        now = datetime.now(timezone.utc).isoformat()
        payload = {"ok": True, "generated_at": now, "data": {}}
        cache_path.write_text(json.dumps(payload))
        from src.api.routes.data_quality import cached_data_quality_ledger
        result = cached_data_quality_ledger()
        assert result is not None
        assert result["ok"] is True
