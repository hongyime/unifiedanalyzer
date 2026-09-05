"""
QA-lane tests for pure functions in:
- src/pipeline/collector_priority_hints.py: _hint_type_for_confidence,
  _policy_for_confidence, _confidence_from_row, _row_get,
  _normalize_source, _clean
- src/pipeline/data_quality_ledger.py: _iso, _max_dt, _age_seconds
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.pipeline.collector_priority_hints import (
    HINT_TYPE_SAME_PERSON_CONFIRMED_100,
    HINT_TYPE_SAME_PERSON_95_99,
    _clean,
    _confidence_from_row,
    _hint_type_for_confidence,
    _normalize_source,
    _policy_for_confidence,
    _row_get,
)
from src.pipeline.data_quality_ledger import _age_seconds, _iso, _max_dt


# ---------------------------------------------------------------------------
# collector_priority_hints._hint_type_for_confidence
# ---------------------------------------------------------------------------

class TestHintTypeForConfidence:
    def test_exactly_1_gives_confirmed(self):
        assert _hint_type_for_confidence(1.0) == HINT_TYPE_SAME_PERSON_CONFIRMED_100

    def test_above_1_gives_confirmed(self):
        assert _hint_type_for_confidence(1.1) == HINT_TYPE_SAME_PERSON_CONFIRMED_100

    def test_below_1_gives_95_99(self):
        assert _hint_type_for_confidence(0.99) == HINT_TYPE_SAME_PERSON_95_99

    def test_zero_gives_95_99(self):
        assert _hint_type_for_confidence(0.0) == HINT_TYPE_SAME_PERSON_95_99


# ---------------------------------------------------------------------------
# collector_priority_hints._policy_for_confidence
# ---------------------------------------------------------------------------

class TestPolicyForConfidence:
    def test_confirmed_100_policy(self):
        policy = _policy_for_confidence(1.0)
        assert "confirmed" in policy.lower()

    def test_95_99_policy(self):
        policy = _policy_for_confidence(0.97)
        assert "95_99" in policy or "probability" in policy


# ---------------------------------------------------------------------------
# collector_priority_hints._confidence_from_row
# ---------------------------------------------------------------------------

class TestConfidenceFromRow:
    def test_float_value_returned(self):
        assert _confidence_from_row({"confidence": 0.9}) == 0.9

    def test_weight_fallback(self):
        result = _confidence_from_row({"weight": 0.8})
        assert result == 0.8

    def test_percentage_normalized(self):
        # >= 10 → divide by 100
        result = _confidence_from_row({"confidence": 95.0})
        assert result is not None
        assert abs(result - 0.95) < 1e-9

    def test_none_returns_none(self):
        assert _confidence_from_row({}) is None

    def test_invalid_string_returns_none(self):
        assert _confidence_from_row({"confidence": "bad"}) is None


# ---------------------------------------------------------------------------
# collector_priority_hints._row_get
# ---------------------------------------------------------------------------

class TestRowGet:
    def test_dict_access(self):
        assert _row_get({"k": "v"}, "k") == "v"

    def test_missing_key_returns_default(self):
        assert _row_get({"k": "v"}, "missing", "default") == "default"

    def test_none_row_returns_default(self):
        assert _row_get(None, "key", "fallback") == "fallback"

    def test_object_with_get_method(self):
        class Row:
            def get(self, k, d=None):
                return "val" if k == "key" else d
        assert _row_get(Row(), "key") == "val"


# ---------------------------------------------------------------------------
# collector_priority_hints._normalize_source
# ---------------------------------------------------------------------------

class TestNormalizeSourceCPH:
    def test_ig_to_instagram(self):
        assert _normalize_source("ig") == "instagram"

    def test_twitter_to_x(self):
        assert _normalize_source("twitter") == "x"

    def test_telegramgo_to_telegram(self):
        assert _normalize_source("telegramgo") == "telegram"

    def test_lowercases(self):
        assert _normalize_source("Instagram") == "instagram"

    def test_none_returns_empty(self):
        assert _normalize_source(None) == ""

    def test_unknown_returned_as_is(self):
        assert _normalize_source("strava") == "strava"


# ---------------------------------------------------------------------------
# collector_priority_hints._clean
# ---------------------------------------------------------------------------

class TestCleanCPH:
    def test_none_returns_none(self):
        assert _clean(None) is None

    def test_empty_string_returns_none(self):
        assert _clean("") is None

    def test_whitespace_only_returns_none(self):
        assert _clean("   ") is None

    def test_strips_whitespace(self):
        assert _clean("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert _clean(42) == "42"


# ---------------------------------------------------------------------------
# data_quality_ledger._iso
# ---------------------------------------------------------------------------

class TestIso:
    def test_datetime_returns_isoformat(self):
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _iso(ts)
        assert "2024-06-01" in result

    def test_none_returns_none(self):
        assert _iso(None) is None

    def test_string_returns_none(self):
        # Strings don't have isoformat method
        assert _iso("2024-06-01") is None

    def test_number_returns_none(self):
        assert _iso(42) is None


# ---------------------------------------------------------------------------
# data_quality_ledger._max_dt
# ---------------------------------------------------------------------------

class TestMaxDt:
    def test_max_of_multiple_dates(self):
        d1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        d2 = datetime(2024, 6, 1, tzinfo=timezone.utc)
        assert _max_dt([d1, d2]) == d2

    def test_none_values_ignored(self):
        d = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _max_dt([None, d, None]) == d

    def test_all_none_returns_none(self):
        assert _max_dt([None, None]) is None

    def test_empty_list_returns_none(self):
        assert _max_dt([]) is None

    def test_single_value(self):
        d = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _max_dt([d]) == d


# ---------------------------------------------------------------------------
# data_quality_ledger._age_seconds
# ---------------------------------------------------------------------------

class TestAgeSeconds:
    def test_none_returns_none(self):
        assert _age_seconds(None) is None

    def test_recent_datetime_returns_small_positive(self):
        ts = datetime.now(timezone.utc)
        result = _age_seconds(ts)
        assert result is not None
        assert result >= 0

    def test_old_datetime_returns_large_positive(self):
        ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = _age_seconds(ts)
        assert result is not None
        assert result > 0

    def test_iso_string_parsed(self):
        ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = _age_seconds(ts.isoformat())
        assert result is not None
        assert result > 0

    def test_invalid_string_returns_none(self):
        assert _age_seconds("not-a-date") is None

    def test_naive_datetime_handled(self):
        ts = datetime(2020, 1, 1)  # no tzinfo
        result = _age_seconds(ts)
        assert result is not None
        assert result > 0
