"""
QA-lane tests for pure functions in:
- src/pipeline/calibration_watchdog.py: _int_env, _float_env
- src/pipeline/conversation_analytics.py: _decode, _iso
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.pipeline.calibration_watchdog import _float_env, _int_env
from src.pipeline.conversation_analytics import _decode, _iso


# ---------------------------------------------------------------------------
# calibration_watchdog._int_env
# ---------------------------------------------------------------------------

class TestIntEnv:
    def test_reads_int(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_ENV", "42")
        assert _int_env("TEST_INT_ENV", 0) == 42

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_INT_ENV_UNSET", raising=False)
        assert _int_env("TEST_INT_ENV_UNSET", 99) == 99

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_ENV", "bad")
        assert _int_env("TEST_INT_ENV", 7) == 7

    def test_zero(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_ENV", "0")
        assert _int_env("TEST_INT_ENV", 5) == 0

    def test_negative(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_ENV", "-3")
        assert _int_env("TEST_INT_ENV", 0) == -3


# ---------------------------------------------------------------------------
# calibration_watchdog._float_env
# ---------------------------------------------------------------------------

class TestFloatEnv:
    def test_reads_float(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_ENV", "3.14")
        assert abs(_float_env("TEST_FLOAT_ENV", 0.0) - 3.14) < 1e-9

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_FLOAT_ENV_UNSET", raising=False)
        assert _float_env("TEST_FLOAT_ENV_UNSET", 0.5) == 0.5

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_ENV", "bad")
        assert _float_env("TEST_FLOAT_ENV", 1.5) == 1.5

    def test_integer_string_returns_float(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_ENV", "7")
        assert _float_env("TEST_FLOAT_ENV", 0.0) == 7.0


# ---------------------------------------------------------------------------
# conversation_analytics._decode
# ---------------------------------------------------------------------------

class TestConversationDecode:
    def test_dict_passthrough(self):
        assert _decode({"a": 1}) == {"a": 1}

    def test_valid_json_string(self):
        assert _decode('{"x": 2}') == {"x": 2}

    def test_json_array_returns_empty(self):
        assert _decode("[1,2]") == {}

    def test_invalid_json_returns_empty(self):
        assert _decode("bad{") == {}

    def test_none_returns_empty(self):
        assert _decode(None) == {}

    def test_bytes_parsed(self):
        assert _decode(b'{"k":"v"}') == {"k": "v"}


# ---------------------------------------------------------------------------
# conversation_analytics._iso
# ---------------------------------------------------------------------------

class TestConversationIso:
    def test_datetime_returns_isoformat(self):
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _iso(ts)
        assert "2024-06-01" in result

    def test_string_returned_as_str(self):
        assert _iso("2024-06-01") == "2024-06-01"

    def test_none_returns_none(self):
        assert _iso(None) is None

    def test_integer_converted_to_str(self):
        result = _iso(42)
        assert result == "42"
