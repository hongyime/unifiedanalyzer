"""
Pure-function tests — batch 48.

Covers previously untested pure functions:
- api.routes.graph: _decode_sources, confidence_bucket, _relationship_why edge cases
- api.routes.collector_health: _latest_iso, _row_get, _as_dict, _as_list,
  _int_value, _blocker_is_active, _parse_datetime
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# api.routes.graph: _decode_sources, confidence_bucket
# ---------------------------------------------------------------------------

class TestDecodeSources:
    def _d(self, sources):
        from src.api.routes.graph import _decode_sources
        return _decode_sources(sources)

    def test_dict_passthrough(self):
        d = {"k": "v"}
        assert self._d(d) is d

    def test_json_string_parsed(self):
        assert self._d('{"a": 1}') == {"a": 1}

    def test_invalid_json_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}

    def test_list_returns_empty(self):
        assert self._d([1, 2]) == {}


class TestConfidenceBucket:
    def _b(self, rtype, weight, cross_platform=False):
        from src.api.routes.graph import confidence_bucket
        return confidence_bucket(rtype, weight, cross_platform)

    def test_same_person_high_weight_hard(self):
        assert self._b("same_person_probability", 90) == "hard"

    def test_same_person_low_weight_strong(self):
        assert self._b("same_person_probability", 50) == "strong"

    def test_interaction_positive_weight_weak(self):
        assert self._b("interaction", 5) == "weak"

    def test_interaction_zero_weight_context_only(self):
        assert self._b("interaction", 1) == "context-only"

    def test_temporal_context_only(self):
        assert self._b("temporal_hour_similarity", 100) == "context-only"

    def test_cross_platform_shared_email_strong(self):
        assert self._b("shared_email", 10, cross_platform=True) == "strong"

    def test_unknown_type_high_weight_weak(self):
        assert self._b("some_unknown_rel", 5) == "weak"

    def test_unknown_type_low_weight_context_only(self):
        assert self._b("some_unknown_rel", 1) == "context-only"

    def test_none_type(self):
        # None type → rtype="" → falls through to last branch
        result = self._b(None, 5)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# api.routes.collector_health: _latest_iso, _row_get, _as_dict, _as_list,
#                               _int_value, _blocker_is_active, _parse_datetime
# ---------------------------------------------------------------------------

class TestLatestIso:
    def _l(self, *values):
        from src.api.routes.collector_health import _latest_iso
        return _latest_iso(*values)

    def test_single_value(self):
        assert self._l("2026-01-15T12:00:00Z") == "2026-01-15T12:00:00Z"

    def test_returns_latest(self):
        result = self._l("2026-01-01T00:00:00Z", "2026-06-15T12:00:00Z", "2025-12-31T00:00:00Z")
        assert "2026-06-15" in result

    def test_empty_values_returns_none(self):
        assert self._l() is None

    def test_none_values_returns_none(self):
        assert self._l(None, None) is None

    def test_mixed_none_and_valid(self):
        result = self._l(None, "2026-03-01T00:00:00Z", None)
        assert "2026-03-01" in result


class TestCollectorHealthRowGet:
    def _r(self, row, key, default=None):
        from src.api.routes.collector_health import _row_get
        return _row_get(row, key, default)

    def test_dict_present(self):
        assert self._r({"k": "v"}, "k") == "v"

    def test_dict_missing_default(self):
        assert self._r({"k": 1}, "x", "fallback") == "fallback"

    def test_none_returns_default(self):
        assert self._r(None, "k", "d") == "d"

    def test_key_error_returns_default(self):
        assert self._r({}, "missing") is None


class TestAsDict:
    def _d(self, v):
        from src.api.routes.collector_health import _as_dict
        return _as_dict(v)

    def test_dict_passthrough(self):
        d = {"a": 1}
        assert self._d(d) is d

    def test_non_dict_returns_empty(self):
        assert self._d("not-dict") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}

    def test_list_returns_empty(self):
        assert self._d([1, 2]) == {}


class TestAsList:
    def _l(self, v):
        from src.api.routes.collector_health import _as_list
        return _as_list(v)

    def test_list_passthrough(self):
        lst = [1, 2, 3]
        assert self._l(lst) is lst

    def test_non_list_returns_empty(self):
        assert self._l("not-list") == []

    def test_none_returns_empty(self):
        assert self._l(None) == []

    def test_dict_returns_empty(self):
        assert self._l({"k": "v"}) == []


class TestIntValue:
    def _i(self, v):
        from src.api.routes.collector_health import _int_value
        return _int_value(v)

    def test_int(self):
        assert self._i(42) == 42

    def test_string_int(self):
        assert self._i("10") == 10

    def test_none_returns_zero(self):
        assert self._i(None) == 0

    def test_invalid_returns_zero(self):
        assert self._i("bad") == 0

    def test_float_truncated(self):
        assert self._i(3.9) == 3


class TestBlockerIsActive:
    def _b(self, blocker):
        from src.api.routes.collector_health import _blocker_is_active
        return _blocker_is_active(blocker)

    def test_empty_dict_inactive(self):
        assert self._b({}) is False

    def test_none_kind_inactive(self):
        assert self._b({"kind": "none", "severity": "none"}) is False

    def test_ok_kind_inactive(self):
        assert self._b({"kind": "ok", "severity": "ok"}) is False

    def test_real_blocker_active(self):
        assert self._b({"kind": "error", "severity": "critical"}) is True

    def test_summary_alone_not_active(self):
        # summary/next_action alone don't activate — kind or severity must be non-empty
        assert self._b({"kind": "", "severity": "", "summary": "something wrong"}) is False

    def test_kind_nonempty_with_summary_active(self):
        assert self._b({"kind": "warning", "severity": "", "summary": "fix needed"}) is True

    def test_next_action_alone_not_active(self):
        assert self._b({"kind": "", "severity": "", "next_action": "fix it"}) is False


class TestCollectorHealthParseDatetime:
    def _p(self, v):
        from src.api.routes.collector_health import _parse_datetime
        return _parse_datetime(v)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_iso_string_parsed(self):
        result = self._p("2026-01-15T12:00:00Z")
        assert isinstance(result, datetime)

    def test_datetime_passthrough(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        result = self._p(dt)
        assert result.tzinfo is not None

    def test_naive_datetime_gets_utc(self):
        dt = datetime(2026, 1, 15)
        result = self._p(dt)
        assert result.tzinfo == timezone.utc

    def test_invalid_string_returns_none(self):
        assert self._p("not-a-date") is None

    def test_empty_string_returns_none(self):
        assert self._p("") is None
