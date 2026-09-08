"""
Pure-function tests — batch 86.

Covers:
- pipeline.decision_replay: _canonical_event_type, _clean, _uuid_list_or_none,
  _as_aware_datetime, _row_value, _walk_snapshots
- pipeline.timeline_builder: _valid_timeline_time
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest


# ---------------------------------------------------------------------------
# pipeline.decision_replay: _canonical_event_type
# ---------------------------------------------------------------------------

class TestCanonicalEventType:
    def _c(self, v):
        from src.pipeline.decision_replay import _canonical_event_type
        return _canonical_event_type(v)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_passthrough_unknown(self):
        assert self._c("my_new_event_type") == "my_new_event_type"

    def test_legacy_alias_resolved(self):
        from src.pipeline.decision_replay import LEGACY_EVENT_TYPE_ALIASES
        if LEGACY_EVENT_TYPE_ALIASES:
            old = next(iter(LEGACY_EVENT_TYPE_ALIASES))
            new = LEGACY_EVENT_TYPE_ALIASES[old]
            assert self._c(old) == new

    def test_non_string_coerced(self):
        result = self._c(42)
        assert result == "42"


# ---------------------------------------------------------------------------
# pipeline.decision_replay: _clean
# ---------------------------------------------------------------------------

class TestDecisionReplayClean:
    def _c(self, v):
        from src.pipeline.decision_replay import _clean
        return _clean(v)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_empty_string_returns_none(self):
        assert self._c("") is None

    def test_whitespace_only_returns_none(self):
        assert self._c("   ") is None

    def test_strips_and_returns(self):
        assert self._c("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert self._c(42) == "42"


# ---------------------------------------------------------------------------
# pipeline.decision_replay: _uuid_list_or_none
# ---------------------------------------------------------------------------

class TestUuidListOrNone:
    def _u(self, values):
        from src.pipeline.decision_replay import _uuid_list_or_none
        return _uuid_list_or_none(values)

    def test_empty_list_returns_none(self):
        assert self._u([]) is None

    def test_valid_uuid_list(self):
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        result = self._u([uuid_str])
        assert result == [uuid_str]

    def test_invalid_uuid_raises(self):
        with pytest.raises(Exception):
            self._u(["not-a-uuid"])

    def test_normalizes_format(self):
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        result = self._u([uuid_str])
        assert result is not None
        assert all("-" in v for v in result)


# ---------------------------------------------------------------------------
# pipeline.decision_replay: _as_aware_datetime
# ---------------------------------------------------------------------------

class TestAsAwareDatetime:
    def _a(self, v):
        from src.pipeline.decision_replay import _as_aware_datetime
        return _as_aware_datetime(v)

    def test_none_returns_none(self):
        assert self._a(None) is None

    def test_empty_string_returns_none(self):
        assert self._a("") is None

    def test_aware_datetime_returned_utc(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = self._a(dt)
        assert result.tzinfo is not None

    def test_naive_datetime_gets_utc(self):
        dt = datetime(2026, 1, 1)
        result = self._a(dt)
        assert result.tzinfo == timezone.utc

    def test_iso_string_parsed(self):
        result = self._a("2026-06-01T12:00:00+00:00")
        assert result is not None
        assert result.year == 2026

    def test_invalid_string_returns_none(self):
        assert self._a("not-a-date") is None


# ---------------------------------------------------------------------------
# pipeline.decision_replay: _row_value
# ---------------------------------------------------------------------------

class TestRowValue:
    def _r(self, row, key):
        from src.pipeline.decision_replay import _row_value
        return _row_value(row, key)

    def test_dict_row_returns_value(self):
        assert self._r({"k": 42}, "k") == 42

    def test_dict_row_missing_returns_none(self):
        assert self._r({}, "k") is None

    def test_subscriptable_row(self):
        # Works on any subscriptable
        assert self._r(["a", "b", "c"], 1) == "b"


# ---------------------------------------------------------------------------
# pipeline.decision_replay: _walk_snapshots
# ---------------------------------------------------------------------------

class TestWalkSnapshots:
    def _w(self, value):
        from src.pipeline.decision_replay import _walk_snapshots
        return list(_walk_snapshots(value))

    def test_empty_dict_yields_nothing(self):
        assert self._w({}) == []

    def test_none_yields_nothing(self):
        assert self._w(None) == []

    def test_snapshot_with_platform_links_yielded(self):
        snap = {"platform_links": [{"source": "instagram"}]}
        result = self._w(snap)
        assert len(result) == 1
        assert result[0] is snap

    def test_nested_snapshot_found(self):
        snap = {"platform_links": [{"source": "telegram"}]}
        nested = {"outer": snap}
        result = self._w(nested)
        assert snap in result

    def test_list_traversed(self):
        snap = {"platform_links": []}
        result = self._w([snap])
        assert snap in result


# ---------------------------------------------------------------------------
# pipeline.timeline_builder: _valid_timeline_time
# ---------------------------------------------------------------------------

class TestValidTimelineTime:
    def _v(self, occurred_at, now=None):
        from src.pipeline.timeline_builder import _valid_timeline_time
        return _valid_timeline_time(occurred_at, now=now)

    def test_none_returns_false(self):
        assert self._v(None) is False

    def test_reasonable_past_date_valid(self):
        dt = datetime(2020, 6, 1, tzinfo=timezone.utc)
        assert self._v(dt) is True

    def test_far_future_invalid(self):
        dt = datetime.now(timezone.utc) + timedelta(days=365 * 10)
        assert self._v(dt) is False

    def test_very_old_date_invalid(self):
        dt = datetime(1900, 1, 1, tzinfo=timezone.utc)
        assert self._v(dt) is False

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2022, 1, 1)  # naive
        result = self._v(dt)
        assert isinstance(result, bool)

    def test_now_is_valid(self):
        now = datetime.now(timezone.utc)
        dt = now - timedelta(hours=1)
        assert self._v(dt, now=now) is True
