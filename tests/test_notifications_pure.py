"""
QA-lane tests for pure helper functions in:
- src/notifications/intelligence.py: _num, _pct, _age, _safe_int, intelligence_run_lines
- src/notifications/merge_bot.py: _make_token, _lookup_pair, callback_data_yes,
  callback_data_no, parse_callback_data, _api_port
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# intelligence._num
# ---------------------------------------------------------------------------

from src.notifications.intelligence import (
    _age,
    _num,
    _pct,
    _safe_int,
    intelligence_run_lines,
)


class TestNum:
    def test_integer(self):
        assert _num(1000) == "1,000"

    def test_zero(self):
        assert _num(0) == "0"

    def test_none(self):
        assert _num(None) == "0"

    def test_string_integer(self):
        assert _num("500") == "500"

    def test_invalid_returns_zero(self):
        assert _num("bad") == "0"


class TestPct:
    def test_basic_percentage(self):
        assert _pct(50, 100) == "50%"

    def test_zero_total_returns_zero_pct(self):
        assert _pct(10, 0) == "0%"

    def test_none_total_returns_zero_pct(self):
        assert _pct(10, None) == "0%"

    def test_rounds_to_nearest_int(self):
        result = _pct(1, 3)  # 33.33%
        assert "33%" in result

    def test_zero_part(self):
        assert _pct(0, 100) == "0%"


class TestAge:
    def test_none_returns_never(self):
        assert _age(None) == "never"

    def test_very_recent_shows_seconds(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = _age(ts)
        assert "s ago" in result

    def test_minutes_ago(self):
        ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = _age(ts)
        assert "m ago" in result

    def test_hours_ago(self):
        ts = datetime.now(timezone.utc) - timedelta(hours=5)
        result = _age(ts)
        assert "h ago" in result

    def test_days_ago(self):
        ts = datetime.now(timezone.utc) - timedelta(days=3)
        result = _age(ts)
        assert "d ago" in result

    def test_naive_datetime_handled(self):
        ts = datetime.now() - timedelta(minutes=10)
        result = _age(ts)
        assert "ago" in result


class TestSafeInt:
    def test_normal_int(self):
        assert _safe_int({"k": 5}, "k") == 5

    def test_missing_key_returns_zero(self):
        assert _safe_int({}, "k") == 0

    def test_none_value_returns_zero(self):
        assert _safe_int({"k": None}, "k") == 0

    def test_bool_returns_zero(self):
        assert _safe_int({"k": True}, "k") == 0

    def test_negative_clamped_to_zero(self):
        assert _safe_int({"k": -5}, "k") == 0

    def test_string_int_coerced(self):
        assert _safe_int({"k": "3"}, "k") == 3


class TestIntelligenceRunLines:
    def test_empty_stats_returns_empty(self):
        assert intelligence_run_lines({}) == []

    def test_with_text_features(self):
        result = intelligence_run_lines({"text_features": 100, "sentiment_features": 50,
                                         "conversation_threads": 10, "alerts": 5})
        assert len(result) >= 1
        assert "100" in result[0] or "text" in result[0].lower()

    def test_with_alert_breakdown_spikes(self):
        result = intelligence_run_lines({
            "text_features": 1,
            "alerts": 3,
            "alert_breakdown": {"emotional_spike": 2}
        })
        assert any("spike" in line.lower() or "emotional" in line.lower() for line in result)

    def test_at_most_2_lines(self):
        result = intelligence_run_lines({
            "text_features": 100, "sentiment_features": 50,
            "conversation_threads": 10, "alerts": 5,
            "alert_breakdown": {"emotional_spike": 3, "face_link_drift": 1}
        })
        assert len(result) <= 2


# ---------------------------------------------------------------------------
# merge_bot: _make_token, _lookup_pair, callback_data_yes/no, parse_callback_data
# ---------------------------------------------------------------------------

from src.notifications.merge_bot import (
    _api_port,
    _lookup_pair,
    _make_token,
    callback_data_no,
    callback_data_yes,
    parse_callback_data,
)

_A = "00000000-0000-0000-0000-000000000001"
_B = "00000000-0000-0000-0000-000000000002"


class TestMakeToken:
    def test_returns_8_char_hex(self):
        token = _make_token(_A, _B)
        assert len(token) == 8
        assert all(c in "0123456789abcdef" for c in token)

    def test_deterministic(self):
        assert _make_token(_A, _B) == _make_token(_A, _B)

    def test_commutative(self):
        assert _make_token(_A, _B) == _make_token(_B, _A)


class TestLookupPair:
    def test_registered_token_found(self):
        token = _make_token(_A, _B)
        result = _lookup_pair(token)
        assert result is not None
        assert set(result) == {_A, _B}

    def test_unknown_token_returns_none(self):
        assert _lookup_pair("xxxxxxxx") is None


class TestCallbackData:
    def test_yes_format(self):
        result = callback_data_yes(_A, _B)
        assert result.startswith("mrg:y:")
        assert len(result) <= 64

    def test_no_format(self):
        result = callback_data_no(_A, _B)
        assert result.startswith("mrg:n:")
        assert len(result) <= 64

    def test_yes_and_no_differ(self):
        assert callback_data_yes(_A, _B) != callback_data_no(_A, _B)

    def test_same_pair_same_token(self):
        y1 = callback_data_yes(_A, _B)
        y2 = callback_data_yes(_A, _B)
        assert y1 == y2


class TestParseCallbackData:
    def test_yes_parsed(self):
        data = callback_data_yes(_A, _B)
        result = parse_callback_data(data)
        assert result is not None
        assert result[0] == "y"

    def test_no_parsed(self):
        data = callback_data_no(_A, _B)
        result = parse_callback_data(data)
        assert result is not None
        assert result[0] == "n"

    def test_invalid_prefix_returns_none(self):
        assert parse_callback_data("other:y:abc12345") is None

    def test_wrong_action_returns_none(self):
        assert parse_callback_data("mrg:x:abc12345") is None

    def test_too_few_parts_returns_none(self):
        assert parse_callback_data("mrg:y") is None

    def test_empty_returns_none(self):
        assert parse_callback_data("") is None


class TestApiPort:
    def test_default_8002(self, monkeypatch):
        monkeypatch.delenv("API_PORT", raising=False)
        assert _api_port() == "8002"

    def test_custom_port(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "9000")
        assert _api_port() == "9000"
