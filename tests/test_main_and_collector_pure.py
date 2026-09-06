"""
QA-lane tests for remaining pure helpers:
- src/main.py: _stale_run_heartbeat_minutes
- src/api/routes/collector_health.py: _targets_by_source
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# main._stale_run_heartbeat_minutes
# ---------------------------------------------------------------------------

from src.main import _stale_run_heartbeat_minutes


class TestStaleRunHeartbeatMinutes:
    def test_default_30(self, monkeypatch):
        monkeypatch.delenv("STALE_RUN_HEARTBEAT_MINUTES", raising=False)
        assert _stale_run_heartbeat_minutes() == 30

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("STALE_RUN_HEARTBEAT_MINUTES", "60")
        assert _stale_run_heartbeat_minutes() == 60

    def test_invalid_falls_back_to_30(self, monkeypatch):
        monkeypatch.setenv("STALE_RUN_HEARTBEAT_MINUTES", "bad")
        assert _stale_run_heartbeat_minutes() == 30

    def test_minimum_1_enforced(self, monkeypatch):
        monkeypatch.setenv("STALE_RUN_HEARTBEAT_MINUTES", "0")
        assert _stale_run_heartbeat_minutes() == 1

    def test_negative_clamped_to_1(self, monkeypatch):
        monkeypatch.setenv("STALE_RUN_HEARTBEAT_MINUTES", "-5")
        assert _stale_run_heartbeat_minutes() == 1


# ---------------------------------------------------------------------------
# collector_health._targets_by_source
# ---------------------------------------------------------------------------

from src.api.routes.collector_health import _targets_by_source

_NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


class TestTargetsBySource:
    def test_empty_list_returns_empty(self):
        assert _targets_by_source([]) == {}

    def test_single_source_grouped(self):
        targets = [
            {"source": "telegram", "status": "fresh", "count": 10, "last_collection": _NOW}
        ]
        result = _targets_by_source(targets)
        assert "telegram" in result
        assert len(result["telegram"]) == 1
        assert result["telegram"][0]["status"] == "fresh"
        assert result["telegram"][0]["count"] == 10

    def test_multiple_targets_same_source(self):
        targets = [
            {"source": "instagram", "status": "fresh", "count": 5, "last_collection": _NOW},
            {"source": "instagram", "status": "stale", "count": 2, "last_collection": None},
        ]
        result = _targets_by_source(targets)
        assert len(result["instagram"]) == 2

    def test_multiple_sources_separated(self):
        targets = [
            {"source": "telegram", "status": "fresh", "count": 5, "last_collection": _NOW},
            {"source": "whatsapp", "status": "stale", "count": 0, "last_collection": None},
        ]
        result = _targets_by_source(targets)
        assert "telegram" in result
        assert "whatsapp" in result

    def test_none_last_collection_becomes_none(self):
        targets = [{"source": "github", "status": "fresh", "count": 3, "last_collection": None}]
        result = _targets_by_source(targets)
        assert result["github"][0]["last_collection"] is None

    def test_datetime_last_collection_isoformatted(self):
        targets = [{"source": "strava", "status": "fresh", "count": 1, "last_collection": _NOW}]
        result = _targets_by_source(targets)
        assert "2024-06-01" in result["strava"][0]["last_collection"]
