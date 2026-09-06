"""
QA-lane tests for remaining pure helpers in src/api/routes/collector_health.py:
- _collector_dashboard_url
- _collector_cookie_vault_url
- _collector_production_summary (empty/minimal surfaces)
"""
from __future__ import annotations

import pytest

from src.api.routes.collector_health import (
    _collector_cookie_vault_url,
    _collector_dashboard_url,
    _collector_production_summary,
)


class TestCollectorDashboardUrl:
    def test_default_url(self, monkeypatch):
        monkeypatch.delenv("COLLECTOR_DASHBOARD_URL", raising=False)
        result = _collector_dashboard_url()
        assert "8700" in result
        assert result.endswith("/") is False

    def test_custom_url(self, monkeypatch):
        monkeypatch.setenv("COLLECTOR_DASHBOARD_URL", "http://custom-host:9000/")
        result = _collector_dashboard_url()
        assert result == "http://custom-host:9000"

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("COLLECTOR_DASHBOARD_URL", "http://host:8700/")
        assert not _collector_dashboard_url().endswith("/")


class TestCollectorCookieVaultUrl:
    def test_default_url(self, monkeypatch):
        monkeypatch.delenv("COLLECTOR_COOKIE_VAULT_URL", raising=False)
        result = _collector_cookie_vault_url()
        assert "8790" in result
        assert not result.endswith("/")

    def test_custom_url(self, monkeypatch):
        monkeypatch.setenv("COLLECTOR_COOKIE_VAULT_URL", "http://vault:9090/")
        result = _collector_cookie_vault_url()
        assert result == "http://vault:9090"


class TestCollectorProductionSummary:
    def test_empty_surfaces_returns_dict(self):
        result = _collector_production_summary({})
        assert isinstance(result, dict)

    def test_keys_present_in_result(self):
        result = _collector_production_summary({})
        # Verify the function returns something structured (not just an empty dict)
        assert isinstance(result, dict)

    def test_no_active_paused_quotas_when_empty(self):
        result = _collector_production_summary({})
        paused = result.get("active_paused_quotas", [])
        assert isinstance(paused, list)
        assert len(paused) == 0

    def test_no_realtime_failed_sources_when_empty(self):
        result = _collector_production_summary({})
        failed = result.get("realtime_failed_sources", [])
        assert isinstance(failed, list)
        assert len(failed) == 0
