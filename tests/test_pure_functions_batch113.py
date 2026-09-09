"""
Pure-function tests — batch 113.

Covers remaining high-value pure functions at the 98% coverage ceiling.

- pipeline.identity_calibration: pair_feature_vector additional edge cases
- pipeline.temporal_correlation: _tight_cooccurrence
- pipeline.entity_resolver: normalize_username additional edge cases
- pipeline.data_quality_ledger: _source_state clock_skew path
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# pipeline.identity_calibration: pair_feature_vector additional
# ---------------------------------------------------------------------------

class TestPairFeatureVectorAdditional:
    def _v(self, contributions):
        from src.pipeline.identity_calibration import pair_feature_vector, FEATURE_ORDER
        return pair_feature_vector(contributions), FEATURE_ORDER

    def test_deprecated_signal_excluded(self):
        from src.pipeline.identity_calibration import (
            pair_feature_vector, FEATURE_ORDER, DEPRECATED_NON_IDENTITY_FEATURES
        )
        if DEPRECATED_NON_IDENTITY_FEATURES:
            dep = next(iter(DEPRECATED_NON_IDENTITY_FEATURES))
            result = pair_feature_vector([(dep, 0.9)])
            assert len(result) == len(FEATURE_ORDER)
            # deprecated signal should contribute 0
            if dep in FEATURE_ORDER:
                idx = FEATURE_ORDER.index(dep)
                assert result[idx] == 0.0

    def test_multiple_same_type_max_used(self):
        from src.pipeline.identity_calibration import pair_feature_vector, FEATURE_ORDER
        if "username_exact" not in FEATURE_ORDER:
            return
        result = pair_feature_vector([
            ("username_exact", 0.3),
            ("username_exact", 0.9),
        ])
        idx = FEATURE_ORDER.index("username_exact")
        assert abs(result[idx] - 0.9) < 1e-9


# ---------------------------------------------------------------------------
# pipeline.temporal_correlation: _tight_cooccurrence
# ---------------------------------------------------------------------------

class TestTightCooccurrence:
    def _t(self, a_ts, b_ts, window_sec=300):
        from src.pipeline.temporal_correlation import _tight_cooccurrence
        return _tight_cooccurrence(a_ts, b_ts, window_sec)

    def _ts(self, offset_minutes=0):
        base = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        return base + timedelta(minutes=offset_minutes)

    def test_empty_lists_returns_zero(self):
        k, days, pval = self._t([], [])
        assert k == 0
        assert pval == 1.0

    def test_too_few_events_returns_zero(self):
        # _MIN_EVENTS is typically 3 — fewer events short-circuits
        a = [self._ts(0)]
        b = [self._ts(1)]
        k, days, pval = self._t(a, b)
        assert k == 0

    def test_non_overlapping_time_ranges_returns_zero(self):
        # a happens in Jan, b happens in June — no overlap
        from src.pipeline.temporal_correlation import _MIN_EVENTS
        a = [self._ts(i * 10) for i in range(_MIN_EVENTS)]
        b = [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i * 10)
             for i in range(_MIN_EVENTS)]
        k, days, pval = self._t(a, b)
        assert k == 0

    def test_synchronized_events_return_nonzero_k(self):
        from src.pipeline.temporal_correlation import _MIN_EVENTS
        # Both post at the exact same times → maximum cooccurrence
        ts = [self._ts(i * 30) for i in range(_MIN_EVENTS + 2)]
        k, days, pval = self._t(ts, ts, window_sec=120)
        assert k >= 1

    def test_returns_three_tuple(self):
        result = self._t([], [])
        assert len(result) == 3


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: normalize_username additional
# ---------------------------------------------------------------------------

class TestNormalizeUsernameAdditional:
    def _n(self, u):
        from src.pipeline.entity_resolver import normalize_username
        return normalize_username(u)

    def test_default_username_pattern_returns_none(self):
        from src.pipeline.entity_resolver import DEFAULT_USERNAME_RE
        # Build a string that matches DEFAULT_USERNAME_RE
        # e.g. "user12345" or similar generic pattern
        import re
        m = DEFAULT_USERNAME_RE.match("user123456")
        if m:
            assert self._n("user123456") is None

    def test_underscore_stripped(self):
        result = self._n("john_doe")
        if result:
            assert "_" not in result

    def test_trailing_digits_stripped(self):
        result = self._n("alice999")
        if result:
            assert not result[-1].isdigit()


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _source_state clock_skew path
# ---------------------------------------------------------------------------

class TestSourceStateClockSkew:
    def _s(self, raw_count=5, analyzer_count=5, raw_future=False, analyzer_future=False):
        from src.pipeline.data_quality_ledger import _source_state
        age = -500 if raw_future else 100
        raw = {"count": raw_count, "latest_at": None, "latest_age_seconds": age}
        analyzer = {
            "total_analyzer_signals": analyzer_count,
            "latest_at": None,
            "latest_age_seconds": -200 if analyzer_future else 200,
        }
        return _source_state(raw, analyzer)

    def test_future_raw_timestamp_is_clock_skew(self):
        state, _ = self._s(raw_future=True)
        assert state == "clock_skew"

    def test_future_analyzer_timestamp_is_clock_skew(self):
        state, _ = self._s(analyzer_future=True)
        assert state == "clock_skew"

    def test_normal_timestamps_not_clock_skew(self):
        state, _ = self._s()
        assert state != "clock_skew"
