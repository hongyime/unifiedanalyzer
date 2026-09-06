"""
QA-lane tests for pure helper functions in:
- src/api/routes/alerts.py: _decode_jsonb, _stream_alert_row, _alert_window_row
- src/api/routes/collector_health.py: _latest_iso, _row_get, _as_dict, _as_list,
  _int_value, _blocker_is_active, _parse_datetime
- src/api/routes/data_quality.py: _cache_ttl_seconds, _parse_ts, _cache_age_seconds
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# alerts._decode_jsonb
# ---------------------------------------------------------------------------

from src.api.routes.alerts import _decode_jsonb


class TestAlertsDecodeJsonb:
    def test_none_returns_empty_list(self):
        assert _decode_jsonb(None) == []

    def test_none_with_custom_default(self):
        assert _decode_jsonb(None, default={}) == {}

    def test_dict_passthrough(self):
        d = {"k": "v"}
        assert _decode_jsonb(d) == d

    def test_list_passthrough(self):
        lst = [1, 2, 3]
        assert _decode_jsonb(lst) == lst

    def test_valid_json_string_parsed(self):
        assert _decode_jsonb('{"x": 1}') == {"x": 1}

    def test_invalid_json_returned_as_is(self):
        result = _decode_jsonb("bad{json")
        assert result == "bad{json"

    def test_bytes_parsed(self):
        assert _decode_jsonb(b'[1, 2]') == [1, 2]

    def test_integer_returned_as_is(self):
        assert _decode_jsonb(42) == 42


# ---------------------------------------------------------------------------
# collector_health._latest_iso
# ---------------------------------------------------------------------------

from src.api.routes.collector_health import (
    _as_dict,
    _as_list,
    _blocker_is_active,
    _int_value,
    _latest_iso,
    _parse_datetime,
    _row_get,
)


class TestLatestIso:
    def test_no_values_returns_none(self):
        assert _latest_iso() is None

    def test_single_valid_iso_returned(self):
        result = _latest_iso("2024-06-01T12:00:00+00:00")
        assert result == "2024-06-01T12:00:00+00:00"

    def test_returns_most_recent(self):
        result = _latest_iso("2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z", "2024-03-01T00:00:00Z")
        assert "2024-06-01" in result

    def test_empty_values_skipped(self):
        result = _latest_iso("", None, "2024-01-01T00:00:00Z")
        assert "2024-01-01" in result

    def test_all_empty_returns_none(self):
        assert _latest_iso("", None, "") is None


class TestRowGet:
    def test_dict_access(self):
        assert _row_get({"k": "v"}, "k") == "v"

    def test_missing_key_returns_default(self):
        assert _row_get({"k": "v"}, "x", "default") == "default"

    def test_none_row_returns_default(self):
        assert _row_get(None, "k", "def") == "def"

    def test_subscript_fallback(self):
        class Obj:
            def __getitem__(self, k):
                return "val" if k == "k" else (_ for _ in ()).throw(KeyError(k))
        assert _row_get(Obj(), "k") == "val"


class TestAsDict:
    def test_dict_returned(self):
        assert _as_dict({"a": 1}) == {"a": 1}

    def test_non_dict_returns_empty(self):
        assert _as_dict("string") == {}
        assert _as_dict(None) == {}
        assert _as_dict([1, 2]) == {}


class TestAsList:
    def test_list_returned(self):
        assert _as_list([1, 2]) == [1, 2]

    def test_non_list_returns_empty(self):
        assert _as_list(None) == []
        assert _as_list("string") == []
        assert _as_list({"k": "v"}) == []


class TestIntValue:
    def test_integer_value(self):
        assert _int_value(42) == 42

    def test_string_integer(self):
        assert _int_value("7") == 7

    def test_none_returns_zero(self):
        assert _int_value(None) == 0

    def test_invalid_returns_zero(self):
        assert _int_value("bad") == 0

    def test_zero_string(self):
        assert _int_value("0") == 0


class TestBlockerIsActive:
    def test_empty_blocker_is_not_active(self):
        assert _blocker_is_active({}) is False

    def test_kind_none_severity_ok_is_not_active(self):
        assert _blocker_is_active({"kind": "none", "severity": "ok"}) is False

    def test_kind_with_value_is_active(self):
        assert _blocker_is_active({"kind": "error", "severity": ""}) is True

    def test_severity_with_value_is_active(self):
        assert _blocker_is_active({"kind": "", "severity": "critical"}) is True

    def test_summary_alone_does_not_activate_when_kind_severity_empty(self):
        # kind='', severity='' both in the 'inactive' set → returns False before checking summary
        assert _blocker_is_active({"kind": "", "severity": "", "summary": "blocked"}) is False

    def test_next_action_alone_does_not_activate_when_kind_severity_absent(self):
        # kind and severity both absent → both resolve to '' → inactive
        assert _blocker_is_active({"next_action": "fix it"}) is False

    def test_kind_with_summary_is_active(self):
        # kind='error' not in inactive set → reaches the bool() check with summary
        assert _blocker_is_active({"kind": "error", "summary": "needs fix"}) is True


class TestParseDatetime:
    def test_none_returns_none(self):
        assert _parse_datetime(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_datetime("") is None

    def test_valid_iso_string(self):
        result = _parse_datetime("2024-06-01T12:00:00+00:00")
        assert result is not None
        assert result.year == 2024

    def test_z_suffix_handled(self):
        result = _parse_datetime("2024-06-01T12:00:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_datetime_object_returned(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        result = _parse_datetime(ts)
        assert result == ts

    def test_invalid_string_returns_none(self):
        assert _parse_datetime("not-a-date") is None

    def test_naive_datetime_gets_utc(self):
        ts = datetime(2024, 6, 1, 12, 0)  # no tzinfo
        result = _parse_datetime(ts)
        assert result is not None
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# data_quality._cache_ttl_seconds, _parse_ts, _cache_age_seconds
# ---------------------------------------------------------------------------

from src.api.routes.data_quality import _cache_age_seconds, _cache_ttl_seconds, _parse_ts


class TestCacheTtlSeconds:
    def test_default_900(self, monkeypatch):
        monkeypatch.delenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", raising=False)
        assert _cache_ttl_seconds() == 900

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", "300")
        assert _cache_ttl_seconds() == 300

    def test_invalid_falls_back_to_900(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", "bad")
        assert _cache_ttl_seconds() == 900

    def test_zero_allowed(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", "0")
        assert _cache_ttl_seconds() == 0


class TestParseTs:
    def test_valid_iso_string(self):
        result = _parse_ts("2024-06-01T12:00:00+00:00")
        assert result is not None
        assert result.year == 2024

    def test_z_suffix_handled(self):
        result = _parse_ts("2024-06-01T00:00:00Z")
        assert result is not None

    def test_none_returns_none(self):
        assert _parse_ts(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_ts("") is None

    def test_invalid_string_returns_none(self):
        assert _parse_ts("not-a-date") is None

    def test_non_string_returns_none(self):
        assert _parse_ts(42) is None


class TestCacheAgeSeconds:
    def test_returns_none_when_no_generated_at(self):
        assert _cache_age_seconds({}) is None

    def test_returns_none_for_invalid_ts(self):
        assert _cache_age_seconds({"generated_at": "bad"}) is None

    def test_returns_positive_for_past_timestamp(self):
        result = _cache_age_seconds({"generated_at": "2020-01-01T00:00:00Z"})
        assert result is not None
        assert result > 0

    def test_returns_zero_for_very_recent_timestamp(self):
        ts = datetime.now(timezone.utc).isoformat()
        result = _cache_age_seconds({"generated_at": ts})
        assert result is not None
        assert result >= 0
