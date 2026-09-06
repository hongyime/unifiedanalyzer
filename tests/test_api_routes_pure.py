"""
QA-lane tests for pure helper functions in src/api/routes/health.py
and src/api/routes/export.py.

No DB or HTTP required — all pure function calls.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# src/api/routes/health.py — _env_int, _iso, _age_seconds
# ---------------------------------------------------------------------------

from src.api.routes.health import _age_seconds, _env_int, _iso


class TestHealthEnvInt:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_HEALTH_INT", raising=False)
        assert _env_int("TEST_HEALTH_INT", 42) == 42

    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("TEST_HEALTH_INT", "99")
        assert _env_int("TEST_HEALTH_INT", 0) == 99

    def test_invalid_uses_default(self, monkeypatch):
        monkeypatch.setenv("TEST_HEALTH_INT", "bad")
        assert _env_int("TEST_HEALTH_INT", 7) == 7

    def test_minimum_enforced(self, monkeypatch):
        monkeypatch.setenv("TEST_HEALTH_INT", "1")
        assert _env_int("TEST_HEALTH_INT", 10, minimum=5) == 5

    def test_above_minimum_untouched(self, monkeypatch):
        monkeypatch.setenv("TEST_HEALTH_INT", "10")
        assert _env_int("TEST_HEALTH_INT", 0, minimum=5) == 10


class TestHealthIso:
    def test_datetime_returns_isoformat(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        result = _iso(ts)
        assert "2024-06-01" in result

    def test_none_returns_none(self):
        assert _iso(None) is None

    def test_falsy_zero_returns_none(self):
        assert _iso(0) is None


class TestHealthAgeSeconds:
    def test_recent_ts_returns_small_positive(self):
        ts = datetime.now(timezone.utc)
        result = _age_seconds(ts)
        assert result is not None
        assert result >= 0

    def test_none_returns_none(self):
        assert _age_seconds(None) is None

    def test_iso_string_parsed(self):
        ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = _age_seconds(ts.isoformat())
        assert result is not None
        assert result > 0

    def test_invalid_string_returns_none(self):
        assert _age_seconds("not-a-date") is None

    def test_naive_datetime_treated_as_utc(self):
        ts = datetime(2020, 1, 1)
        result = _age_seconds(ts)
        assert result is not None
        assert result > 0

    def test_result_floored_at_zero(self):
        # Slightly future timestamp should return 0, not negative
        ts = datetime.now(timezone.utc) + timedelta(seconds=1)
        result = _age_seconds(ts)
        assert result is not None
        assert result >= 0


# ---------------------------------------------------------------------------
# src/api/routes/export.py — _hash_value
# ---------------------------------------------------------------------------

from src.api.routes.export import _hash_value


class TestExportHashValue:
    def test_none_returns_none(self):
        assert _hash_value(None) is None

    def test_empty_string_returns_none(self):
        assert _hash_value("") is None

    def test_returns_16_char_hex(self):
        result = _hash_value("https://example.com")
        assert result is not None
        assert len(result) == 16

    def test_deterministic(self):
        assert _hash_value("test") == _hash_value("test")

    def test_different_inputs_differ(self):
        assert _hash_value("a") != _hash_value("b")

    def test_known_value(self):
        expected = hashlib.sha256("hello".encode()).hexdigest()[:16]
        assert _hash_value("hello") == expected
