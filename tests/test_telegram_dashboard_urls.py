"""
QA-lane tests for remaining pure helpers in src/notifications/telegram.py:
- get_dashboard_url: uses _TAILSCALE_IP and _API_PORT module globals
- get_collector_dashboard_url: uses _TAILSCALE_IP and _COLLECTOR_PORT globals
"""
from __future__ import annotations

import pytest
import src.notifications.telegram as tg


class TestGetDashboardUrl:
    def test_default_fallback_to_localhost(self, monkeypatch):
        monkeypatch.setattr(tg, "_TAILSCALE_IP", None)
        monkeypatch.setattr(tg, "_API_PORT", "8002")
        result = tg.get_dashboard_url()
        assert result == "http://127.0.0.1:8002"

    def test_tailscale_ip_used_when_set(self, monkeypatch):
        monkeypatch.setattr(tg, "_TAILSCALE_IP", "100.64.1.2")
        monkeypatch.setattr(tg, "_API_PORT", "8002")
        result = tg.get_dashboard_url()
        assert result == "http://100.64.1.2:8002"

    def test_custom_api_port(self, monkeypatch):
        monkeypatch.setattr(tg, "_TAILSCALE_IP", None)
        monkeypatch.setattr(tg, "_API_PORT", "9000")
        result = tg.get_dashboard_url()
        assert "9000" in result

    def test_url_starts_with_http(self, monkeypatch):
        monkeypatch.setattr(tg, "_TAILSCALE_IP", None)
        monkeypatch.setattr(tg, "_API_PORT", "8002")
        assert tg.get_dashboard_url().startswith("http://")


class TestGetCollectorDashboardUrl:
    def test_default_fallback_to_localhost(self, monkeypatch):
        monkeypatch.setattr(tg, "_TAILSCALE_IP", None)
        monkeypatch.setattr(tg, "_COLLECTOR_PORT", "8700")
        result = tg.get_collector_dashboard_url()
        assert result == "http://127.0.0.1:8700"

    def test_tailscale_ip_used_when_set(self, monkeypatch):
        monkeypatch.setattr(tg, "_TAILSCALE_IP", "100.64.1.2")
        monkeypatch.setattr(tg, "_COLLECTOR_PORT", "8700")
        result = tg.get_collector_dashboard_url()
        assert "100.64.1.2" in result

    def test_custom_collector_port(self, monkeypatch):
        monkeypatch.setattr(tg, "_TAILSCALE_IP", None)
        monkeypatch.setattr(tg, "_COLLECTOR_PORT", "9700")
        result = tg.get_collector_dashboard_url()
        assert "9700" in result

    def test_differs_from_analyzer_url(self, monkeypatch):
        monkeypatch.setattr(tg, "_TAILSCALE_IP", None)
        monkeypatch.setattr(tg, "_API_PORT", "8002")
        monkeypatch.setattr(tg, "_COLLECTOR_PORT", "8700")
        analyzer_url = tg.get_dashboard_url()
        collector_url = tg.get_collector_dashboard_url()
        assert analyzer_url != collector_url
