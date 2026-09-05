"""
QA-lane tests for pure functions in src/pipeline/temporal_correlation.py.

Covers:
- _decode_meta: dict/str/bytes/invalid handling
- _cosine: zero vectors, identical, orthogonal, partial overlap
- _hour_idf_weights: shape, IDF scaling, floor value
- _weighted_cosine: delegates to _cosine with scaled inputs
- _date_span: simple day count
- _tight_cooccurrence: below-min-events early exit, no overlap, match
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from src.pipeline.temporal_correlation import (
    _MIN_EVENTS,
    _cosine,
    _date_span,
    _decode_meta,
    _hour_idf_weights,
    _tight_cooccurrence,
    _weighted_cosine,
)


# ---------------------------------------------------------------------------
# _decode_meta
# ---------------------------------------------------------------------------

class TestDecodeMeta:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert _decode_meta(d) == d

    def test_valid_json_string(self):
        assert _decode_meta('{"x": 2}') == {"x": 2}

    def test_json_array_returns_empty(self):
        assert _decode_meta("[1,2]") == {}

    def test_invalid_json_returns_empty(self):
        assert _decode_meta("bad{") == {}

    def test_none_returns_empty(self):
        assert _decode_meta(None) == {}

    def test_bytes_parsed(self):
        assert _decode_meta(b'{"k":"v"}') == {"k": "v"}

    def test_integer_returns_empty(self):
        assert _decode_meta(42) == {}


# ---------------------------------------------------------------------------
# _cosine (list-based, unlike content_fingerprint's dict-based)
# ---------------------------------------------------------------------------

class TestCosine:
    def test_identical_vectors_give_1(self):
        a = [1.0, 0.0, 2.0]
        assert abs(_cosine(a, a) - 1.0) < 1e-9

    def test_orthogonal_vectors_give_0(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine(a, b) == 0.0

    def test_zero_vector_gives_0(self):
        assert _cosine([0.0, 0.0], [1.0, 2.0]) == 0.0
        assert _cosine([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_partial_overlap_between_0_and_1(self):
        a = [1.0, 1.0, 0.0]
        b = [1.0, 0.0, 1.0]
        sim = _cosine(a, b)
        assert 0.0 < sim < 1.0

    def test_symmetry(self):
        a = [1.0, 2.0, 0.5]
        b = [0.5, 1.0, 3.0]
        assert abs(_cosine(a, b) - _cosine(b, a)) < 1e-9

    def test_24_hour_uniform_vs_uniform_gives_1(self):
        uniform = [1.0 / 24] * 24
        assert abs(_cosine(uniform, uniform) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# _hour_idf_weights
# ---------------------------------------------------------------------------

class TestHourIdfWeights:
    def test_returns_24_weights(self):
        weights = _hour_idf_weights({"e1": [1.0] * 24})
        assert len(weights) == 24

    def test_all_weights_positive(self):
        dists = {f"e{i}": [float(i % 2)] * 24 for i in range(5)}
        weights = _hour_idf_weights(dists)
        assert all(w > 0 for w in weights)

    def test_rare_hour_gets_higher_weight_than_common_hour(self):
        # All 5 entities post at hour 0; only 1 posts at hour 1
        dists = {}
        for i in range(5):
            dist = [0.0] * 24
            dist[0] = 1.0  # hour 0: everyone posts here
            if i == 0:
                dist[1] = 1.0  # hour 1: only entity 0
            dists[f"e{i}"] = dist
        weights = _hour_idf_weights(dists)
        assert weights[1] > weights[0]

    def test_floor_applied_when_all_entities_post_at_hour(self):
        # All entities post at every hour → weight is floored at 0.05
        n = 10
        dists = {f"e{i}": [1.0] * 24 for i in range(n)}
        weights = _hour_idf_weights(dists)
        assert all(w >= 0.05 for w in weights)

    def test_empty_dists_uses_floor(self):
        weights = _hour_idf_weights({})
        assert len(weights) == 24
        assert all(w >= 0.05 for w in weights)


# ---------------------------------------------------------------------------
# _weighted_cosine
# ---------------------------------------------------------------------------

class TestWeightedCosine:
    def test_zero_weights_give_zero(self):
        a = [1.0] * 24
        b = [1.0] * 24
        w = [0.0] * 24
        assert _weighted_cosine(a, b, w) == 0.0

    def test_identical_with_uniform_weights_gives_1(self):
        a = [1.0] * 24
        w = [1.0] * 24
        assert abs(_weighted_cosine(a, a, w) - 1.0) < 1e-9

    def test_rare_hour_agreement_boosts_score(self):
        # a and b both active only at hour 3 (rare)
        a = [0.0] * 24
        b = [0.0] * 24
        a[3] = 1.0
        b[3] = 1.0
        w_high = [0.0] * 24
        w_high[3] = 10.0  # high weight for hour 3
        w_low = [0.0] * 24
        w_low[3] = 0.1
        sim_high = _weighted_cosine(a, b, w_high)
        sim_low = _weighted_cosine(a, b, w_low)
        # Both should be 1.0 (identical histograms) regardless of weight scaling
        assert abs(sim_high - 1.0) < 1e-9
        assert abs(sim_low - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# _date_span
# ---------------------------------------------------------------------------

class TestDateSpan:
    def test_same_day_is_1(self):
        d = date(2024, 1, 1)
        assert _date_span(d, d) == 1

    def test_two_consecutive_days_is_2(self):
        assert _date_span(date(2024, 1, 1), date(2024, 1, 2)) == 2

    def test_one_month_span(self):
        assert _date_span(date(2024, 1, 1), date(2024, 1, 31)) == 31

    def test_year_span(self):
        assert _date_span(date(2024, 1, 1), date(2024, 12, 31)) == 366  # 2024 leap year


# ---------------------------------------------------------------------------
# _tight_cooccurrence
# ---------------------------------------------------------------------------

_BASE = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(offset_hours: float) -> datetime:
    return _BASE + timedelta(hours=offset_hours)


class TestTightCooccurrence:
    def test_below_min_events_returns_early(self):
        # _MIN_EVENTS events required; provide fewer
        a = [_ts(i) for i in range(_MIN_EVENTS - 1)]
        b = [_ts(i) for i in range(10)]
        k, days, pval = _tight_cooccurrence(a, b, window_sec=300)
        assert k == 0
        assert pval == 1.0

    def test_no_overlap_in_time_range_returns_zero(self):
        # a events all before b events with no overlap
        a = [_ts(i) for i in range(_MIN_EVENTS)]
        b = [_ts(i + 1000) for i in range(_MIN_EVENTS)]
        k, days, pval = _tight_cooccurrence(a, b, window_sec=300)
        assert k == 0

    def test_matching_events_within_window_counted(self):
        # a and b events 1 minute apart, window is 5 minutes
        a = [_ts(i * 24) for i in range(_MIN_EVENTS + 5)]    # one per day
        b = [_ts(i * 24 + 1/60) for i in range(_MIN_EVENTS + 5)]  # 1 min after each a
        k, days, pval = _tight_cooccurrence(a, b, window_sec=600)
        assert k > 0
        assert days > 0
        assert 0.0 <= pval <= 1.0

    def test_events_outside_window_not_counted(self):
        # a and b events 2 hours apart, window is 5 minutes
        a = [_ts(i * 24) for i in range(_MIN_EVENTS + 5)]
        b = [_ts(i * 24 + 2) for i in range(_MIN_EVENTS + 5)]  # 2 hours after a
        k, days, pval = _tight_cooccurrence(a, b, window_sec=300)
        assert k == 0

    def test_k_and_days_both_positive_on_match(self):
        # Spread over multiple days
        a = [_ts(i * 24) for i in range(_MIN_EVENTS + 10)]
        b = [_ts(i * 24 + 0.01) for i in range(_MIN_EVENTS + 10)]  # 36 sec after
        k, days, pval = _tight_cooccurrence(a, b, window_sec=300)
        assert k > 0
        assert days >= 1
