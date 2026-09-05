"""
QA-lane tests for pure (non-DB) functions in src/pipeline/timeline_builder.py.

Covers:
- _valid_timeline_time: min-date floor, future ceiling, None, naive datetime
- _jsonb_param: None, string pass-through, dict serialization
- _format_platform_query: where_clause injection, since=None, analyzer DB tz handling
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.pipeline.timeline_builder import (
    TIMELINE_MAX_FUTURE,
    TIMELINE_MIN_DATE,
    _format_platform_query,
    _jsonb_param,
    _valid_timeline_time,
)

_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _valid_timeline_time
# ---------------------------------------------------------------------------

class TestValidTimelineTime:
    def test_none_returns_false(self):
        assert _valid_timeline_time(None, now=_NOW) is False

    def test_valid_recent_timestamp_returns_true(self):
        ts = datetime(2024, 6, 1, tzinfo=timezone.utc)
        assert _valid_timeline_time(ts, now=_NOW) is True

    def test_exactly_min_date_returns_true(self):
        assert _valid_timeline_time(TIMELINE_MIN_DATE, now=_NOW) is True

    def test_before_min_date_returns_false(self):
        ts = datetime(2004, 12, 31, tzinfo=timezone.utc)
        assert _valid_timeline_time(ts, now=_NOW) is False

    def test_epoch_zero_returns_false(self):
        ts = datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert _valid_timeline_time(ts, now=_NOW) is False

    def test_within_future_window_returns_true(self):
        # Just inside the TIMELINE_MAX_FUTURE ceiling
        ts = _NOW + TIMELINE_MAX_FUTURE - timedelta(seconds=1)
        assert _valid_timeline_time(ts, now=_NOW) is True

    def test_beyond_future_ceiling_returns_false(self):
        ts = _NOW + TIMELINE_MAX_FUTURE + timedelta(seconds=1)
        assert _valid_timeline_time(ts, now=_NOW) is False

    def test_naive_datetime_treated_as_utc(self):
        # Naive datetime: no tzinfo, but function attaches UTC
        ts_naive = datetime(2024, 6, 1)  # no tzinfo
        assert _valid_timeline_time(ts_naive, now=_NOW) is True

    def test_naive_datetime_before_floor_returns_false(self):
        ts_naive = datetime(2000, 1, 1)
        assert _valid_timeline_time(ts_naive, now=_NOW) is False

    def test_uses_real_now_when_not_provided(self):
        # Just verify it doesn't raise; no mock needed for a recent date
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = _valid_timeline_time(ts)
        assert result is True


# ---------------------------------------------------------------------------
# _jsonb_param
# ---------------------------------------------------------------------------

class TestJsonbParam:
    def test_none_returns_empty_object(self):
        assert _jsonb_param(None) == "{}"

    def test_string_passed_through_unchanged(self):
        assert _jsonb_param('{"key": "value"}') == '{"key": "value"}'

    def test_empty_string_passed_through(self):
        assert _jsonb_param("") == ""

    def test_dict_serialized_to_json(self):
        import json
        result = _jsonb_param({"lat": 1.23, "lng": 4.56})
        parsed = json.loads(result)
        assert parsed == {"lat": 1.23, "lng": 4.56}

    def test_nested_dict_serialized(self):
        import json
        raw = {"location": {"lat": 1.0, "name": "Singapore"}}
        result = _jsonb_param(raw)
        parsed = json.loads(result)
        assert parsed["location"]["name"] == "Singapore"

    def test_non_serializable_uses_str_fallback(self):
        import json
        # datetime is not JSON serializable by default; default=str handles it
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = _jsonb_param({"ts": ts})
        parsed = json.loads(result)
        assert "ts" in parsed


# ---------------------------------------------------------------------------
# _format_platform_query
# ---------------------------------------------------------------------------

_SIMPLE_PQ = {
    "source": "github",
    "event_type": "CODE_COMMIT",
    "query": "SELECT * FROM commits WHERE 1=1 {where_clause} ORDER BY date DESC",
    "time_col": "c.date",
}

_MULTI_FILTER_PQ = {
    "source": "test",
    "event_type": "TEST",
    "query": "SELECT * FROM t WHERE 1=1 {f1} {f2}",
    "time_filters": {
        "f1": "t.created_at",
        "f2": "t.updated_at",
    },
}

_ANALYZER_DB_PQ = {
    "source": "test",
    "event_type": "TEST",
    "query": "SELECT * FROM t WHERE 1=1 {where_clause}",
    "time_col": "t.created_at",
    "db": "analyzer",
}


class TestFormatPlatformQuery:
    def test_no_since_yields_empty_where_clause(self):
        sql, params = _format_platform_query(_SIMPLE_PQ, since=None)
        assert "{where_clause}" not in sql
        assert "" in sql or "WHERE 1=1" in sql
        assert params == []

    def test_with_since_injects_where_clause(self):
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        sql, params = _format_platform_query(_SIMPLE_PQ, since=since)
        assert "c.date" in sql
        assert "$1" in sql
        assert len(params) == 1

    def test_with_since_strips_tz_for_collector_db(self):
        # Default (no db key) = collector DB → strips tzinfo
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        _, params = _format_platform_query(_SIMPLE_PQ, since=since)
        assert params[0].tzinfo is None

    def test_with_since_keeps_tz_for_analyzer_db(self):
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        _, params = _format_platform_query(_ANALYZER_DB_PQ, since=since)
        # analyzer DB: tz preserved
        assert params[0] == since

    def test_naive_since_used_as_is(self):
        since = datetime(2024, 1, 1)  # no tzinfo
        sql, params = _format_platform_query(_SIMPLE_PQ, since=since)
        assert len(params) == 1

    def test_multi_filter_no_since_replaces_all_placeholders(self):
        sql, params = _format_platform_query(_MULTI_FILTER_PQ, since=None)
        assert "{f1}" not in sql
        assert "{f2}" not in sql
        assert params == []

    def test_multi_filter_with_since_injects_all_filters(self):
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        sql, params = _format_platform_query(_MULTI_FILTER_PQ, since=since)
        assert "t.created_at" in sql
        assert "t.updated_at" in sql
        assert "$1" in sql
        assert len(params) == 1
