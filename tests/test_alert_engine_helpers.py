"""
QA-lane tests for pure helpers in:
- src/pipeline/alert_engine.py: _decode_meta, _env_bool, _env_float, _env_int
- src/pipeline/auto_labeler.py: _is_enabled
"""
from __future__ import annotations

import pytest

from src.pipeline.alert_engine import (
    _decode_meta,
    _env_bool,
    _env_float,
    _env_int,
)
from src.pipeline.auto_labeler import _is_enabled


# ---------------------------------------------------------------------------
# alert_engine._decode_meta (same pattern as temporal_correlation._decode_meta)
# ---------------------------------------------------------------------------

class TestAlertEngineDecodeMeta:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert _decode_meta(d) == d

    def test_valid_json_string(self):
        assert _decode_meta('{"x": 2}') == {"x": 2}

    def test_json_array_returns_empty(self):
        assert _decode_meta("[1, 2]") == {}

    def test_invalid_json_returns_empty(self):
        assert _decode_meta("bad{") == {}

    def test_bytes_parsed(self):
        assert _decode_meta(b'{"k": "v"}') == {"k": "v"}

    def test_none_returns_empty(self):
        assert _decode_meta(None) == {}

    def test_integer_returns_empty(self):
        assert _decode_meta(42) == {}


# ---------------------------------------------------------------------------
# alert_engine._env_bool
# ---------------------------------------------------------------------------

class TestEnvBool:
    def test_true_string(self, monkeypatch):
        monkeypatch.setenv("TEST_BOOL", "true")
        assert _env_bool("TEST_BOOL") is True

    def test_one_string(self, monkeypatch):
        monkeypatch.setenv("TEST_BOOL", "1")
        assert _env_bool("TEST_BOOL") is True

    def test_yes_string(self, monkeypatch):
        monkeypatch.setenv("TEST_BOOL", "yes")
        assert _env_bool("TEST_BOOL") is True

    def test_false_string(self, monkeypatch):
        monkeypatch.setenv("TEST_BOOL", "false")
        assert _env_bool("TEST_BOOL") is False

    def test_zero_string(self, monkeypatch):
        monkeypatch.setenv("TEST_BOOL", "0")
        assert _env_bool("TEST_BOOL") is False

    def test_default_true_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_BOOL_UNSET", raising=False)
        assert _env_bool("TEST_BOOL_UNSET", default=True) is True

    def test_default_false_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_BOOL_UNSET", raising=False)
        assert _env_bool("TEST_BOOL_UNSET", default=False) is False

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("TEST_BOOL", "TRUE")
        assert _env_bool("TEST_BOOL") is True


# ---------------------------------------------------------------------------
# alert_engine._env_float
# ---------------------------------------------------------------------------

class TestEnvFloat:
    def test_reads_float(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT", "3.14")
        assert abs(_env_float("TEST_FLOAT", 0.0) - 3.14) < 1e-9

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_FLOAT_UNSET", raising=False)
        assert _env_float("TEST_FLOAT_UNSET", 2.5) == 2.5

    def test_integer_string_returns_float(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT", "7")
        assert _env_float("TEST_FLOAT", 0.0) == 7.0

    def test_negative_value(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT", "-1.5")
        assert _env_float("TEST_FLOAT", 0.0) == -1.5


# ---------------------------------------------------------------------------
# alert_engine._env_int
# ---------------------------------------------------------------------------

class TestEnvInt:
    def test_reads_int(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "42")
        assert _env_int("TEST_INT", 0) == 42

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_INT_UNSET", raising=False)
        assert _env_int("TEST_INT_UNSET", 10) == 10

    def test_zero(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "0")
        assert _env_int("TEST_INT", 99) == 0

    def test_negative(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "-5")
        assert _env_int("TEST_INT", 0) == -5


# ---------------------------------------------------------------------------
# auto_labeler._is_enabled
# ---------------------------------------------------------------------------

class TestAutoLabelerIsEnabled:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("AUTO_LABEL_ENABLED", raising=False)
        assert _is_enabled() is False

    def test_enabled_when_set_to_1(self, monkeypatch):
        monkeypatch.setenv("AUTO_LABEL_ENABLED", "1")
        assert _is_enabled() is True

    def test_disabled_when_set_to_0(self, monkeypatch):
        monkeypatch.setenv("AUTO_LABEL_ENABLED", "0")
        assert _is_enabled() is False

    def test_disabled_when_set_to_true_string(self, monkeypatch):
        # Only "1" enables it — "true" does not
        monkeypatch.setenv("AUTO_LABEL_ENABLED", "true")
        assert _is_enabled() is False
