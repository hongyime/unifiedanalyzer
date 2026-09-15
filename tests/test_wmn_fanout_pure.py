"""Pure-function tests for src/pipeline/wmn_fanout.py.

Covers the env-config helpers, the site-list loader (with monkeypatched
_DATA_PATH so we don't touch the real vendored 258 KB file), and the
per-site probe against a mocked httpx client.  ``run_wmn_fanout`` itself
requires a live DB pool and is exercised in an integration test elsewhere.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.pipeline import wmn_fanout as wmn


# ---------------------------------------------------------------------------
# _is_enabled — default OFF (network-heavy, opt-in)
# ---------------------------------------------------------------------------

class TestWmnIsEnabled:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("WMN_FANOUT_ENABLED", raising=False)
        assert wmn._is_enabled() is False

    def test_enabled_via_1(self, monkeypatch):
        monkeypatch.setenv("WMN_FANOUT_ENABLED", "1")
        assert wmn._is_enabled() is True

    def test_disabled_via_0(self, monkeypatch):
        monkeypatch.setenv("WMN_FANOUT_ENABLED", "0")
        assert wmn._is_enabled() is False


# ---------------------------------------------------------------------------
# _cfg_int / _cfg_float — tolerant of garbage
# ---------------------------------------------------------------------------

class TestConfigHelpers:
    def test_cfg_int_default(self, monkeypatch):
        monkeypatch.delenv("WMN_FANOUT_MAX_PER_RUN", raising=False)
        assert wmn._cfg_int("WMN_FANOUT_MAX_PER_RUN", 1) == 1

    def test_cfg_int_override(self, monkeypatch):
        monkeypatch.setenv("WMN_FANOUT_MAX_PER_RUN", "7")
        assert wmn._cfg_int("WMN_FANOUT_MAX_PER_RUN", 1) == 7

    def test_cfg_int_bad_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("WMN_FANOUT_MAX_PER_RUN", "not-a-number")
        assert wmn._cfg_int("WMN_FANOUT_MAX_PER_RUN", 4) == 4

    def test_cfg_float_default(self, monkeypatch):
        monkeypatch.delenv("WMN_FANOUT_SITE_TIMEOUT_S", raising=False)
        assert wmn._cfg_float("WMN_FANOUT_SITE_TIMEOUT_S", 5.0) == 5.0

    def test_cfg_float_override(self, monkeypatch):
        monkeypatch.setenv("WMN_FANOUT_SITE_TIMEOUT_S", "2.5")
        assert wmn._cfg_float("WMN_FANOUT_SITE_TIMEOUT_S", 5.0) == 2.5

    def test_cfg_float_bad_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("WMN_FANOUT_SITE_TIMEOUT_S", "x.y")
        assert wmn._cfg_float("WMN_FANOUT_SITE_TIMEOUT_S", 5.0) == 5.0


# ---------------------------------------------------------------------------
# _load_sites — cache + valid-flag filter + missing-file graceful fallback
# ---------------------------------------------------------------------------

class TestLoadSites:
    def _make_json_file(self, tmp_path: Path, sites: list[dict]) -> Path:
        path = tmp_path / "wmn-data.json"
        path.write_text(json.dumps({"sites": sites}), encoding="utf-8")
        return path

    def test_valid_sites_kept(self, tmp_path, monkeypatch):
        path = self._make_json_file(tmp_path, [
            {"name": "A", "uri_check": "https://a/{account}",
             "e_code": 200, "e_string": "ok", "valid": True},
            {"name": "B", "uri_check": "https://b/{account}",
             "e_code": 200, "e_string": "ok"},  # no valid → treated as valid
        ])
        monkeypatch.setattr(wmn, "_DATA_PATH", path)
        monkeypatch.setattr(wmn, "_sites_cache", None)
        loaded = wmn._load_sites()
        assert [s["name"] for s in loaded] == ["A", "B"]

    def test_valid_false_dropped(self, tmp_path, monkeypatch):
        path = self._make_json_file(tmp_path, [
            {"name": "Good", "uri_check": "https://g/{account}",
             "e_code": 200, "e_string": "ok", "valid": True},
            {"name": "Broken", "uri_check": "https://b/{account}",
             "e_code": 200, "e_string": "ok", "valid": False},
        ])
        monkeypatch.setattr(wmn, "_DATA_PATH", path)
        monkeypatch.setattr(wmn, "_sites_cache", None)
        loaded = wmn._load_sites()
        assert [s["name"] for s in loaded] == ["Good"]

    def test_entries_without_uri_check_dropped(self, tmp_path, monkeypatch):
        path = self._make_json_file(tmp_path, [
            {"name": "Empty", "e_code": 200, "e_string": "ok"},
            {"name": "Good", "uri_check": "https://g/{account}",
             "e_code": 200, "e_string": "ok"},
        ])
        monkeypatch.setattr(wmn, "_DATA_PATH", path)
        monkeypatch.setattr(wmn, "_sites_cache", None)
        loaded = wmn._load_sites()
        assert [s["name"] for s in loaded] == ["Good"]

    def test_missing_file_returns_empty_and_warns(self, tmp_path, monkeypatch, caplog):
        path = tmp_path / "does-not-exist.json"
        monkeypatch.setattr(wmn, "_DATA_PATH", path)
        monkeypatch.setattr(wmn, "_sites_cache", None)
        caplog.set_level("WARNING")
        loaded = wmn._load_sites()
        assert loaded == []
        assert any("WhatsMyName data missing" in r.message for r in caplog.records)

    def test_result_cached(self, tmp_path, monkeypatch):
        path = self._make_json_file(tmp_path, [
            {"name": "A", "uri_check": "https://a/{account}",
             "e_code": 200, "e_string": "ok"},
        ])
        monkeypatch.setattr(wmn, "_DATA_PATH", path)
        monkeypatch.setattr(wmn, "_sites_cache", None)
        first = wmn._load_sites()
        # Simulate the file being deleted after the first read; cache must
        # keep returning the loaded list.
        path.unlink()
        second = wmn._load_sites()
        assert first is second


# ---------------------------------------------------------------------------
# _check_site — httpx mocked
# ---------------------------------------------------------------------------

def _fake_response(status: int, text: str) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


class TestCheckSite:
    @pytest.mark.asyncio
    async def test_hit_when_code_and_string_match(self):
        import asyncio
        site = {"name": "T", "uri_check": "https://t/{account}",
                "e_code": 200, "e_string": "profile:", "cat": "tech"}
        client = MagicMock()
        client.get = AsyncMock(return_value=_fake_response(200, "profile: found"))
        result = await wmn._check_site(
            client, asyncio.Semaphore(1), site, "alice", site_timeout=5,
        )
        assert result == {"site": "T", "url": "https://t/alice", "cat": "tech"}

    @pytest.mark.asyncio
    async def test_miss_when_code_differs(self):
        import asyncio
        site = {"name": "T", "uri_check": "https://t/{account}",
                "e_code": 200, "e_string": "profile:"}
        client = MagicMock()
        client.get = AsyncMock(return_value=_fake_response(404, "profile: found"))
        result = await wmn._check_site(
            client, asyncio.Semaphore(1), site, "alice", site_timeout=5,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_miss_when_estring_absent(self):
        import asyncio
        site = {"name": "T", "uri_check": "https://t/{account}",
                "e_code": 200, "e_string": "profile:"}
        client = MagicMock()
        client.get = AsyncMock(return_value=_fake_response(200, "page not found"))
        result = await wmn._check_site(
            client, asyncio.Semaphore(1), site, "alice", site_timeout=5,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_uri_pretty_used_when_present(self):
        import asyncio
        site = {"name": "T",
                "uri_check": "https://api.t/verify/{account}",
                "uri_pretty": "https://t.example/{account}",
                "e_code": 200, "e_string": "ok"}
        client = MagicMock()
        client.get = AsyncMock(return_value=_fake_response(200, "ok"))
        result = await wmn._check_site(
            client, asyncio.Semaphore(1), site, "alice", site_timeout=5,
        )
        assert result["url"] == "https://t.example/alice"

    @pytest.mark.asyncio
    async def test_httpx_error_returns_none(self):
        import asyncio
        import httpx
        site = {"name": "T", "uri_check": "https://t/{account}",
                "e_code": 200, "e_string": "ok"}
        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("nope"))
        result = await wmn._check_site(
            client, asyncio.Semaphore(1), site, "alice", site_timeout=5,
        )
        assert result is None


# ---------------------------------------------------------------------------
# run_wmn_fanout — early-return paths (no DB needed)
# ---------------------------------------------------------------------------

class TestRunWmnFanoutSkip:
    @pytest.mark.asyncio
    async def test_returns_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setenv("WMN_FANOUT_ENABLED", "0")
        result = await wmn.run_wmn_fanout()
        assert result["skipped"] == "disabled"

    @pytest.mark.asyncio
    async def test_returns_skipped_when_data_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WMN_FANOUT_ENABLED", "1")
        monkeypatch.setattr(wmn, "_DATA_PATH", tmp_path / "missing.json")
        result = await wmn.run_wmn_fanout()
        assert result["skipped"] == "wmn_data_missing"
