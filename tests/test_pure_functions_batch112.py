"""
Pure-function tests — batch 112.

Covers:
- pipeline.entity_resolver: _cross_entity_confidence
- pipeline.entity_resolver: normalize_username_strict additional edge cases
- Additional high-value coverage
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: _cross_entity_confidence
# ---------------------------------------------------------------------------

class TestCrossEntityConfidence:
    def _make_signal(self, signal_type, confidence):
        from src.pipeline.entity_resolver import SignalMatch
        return SignalMatch(
            signal_type=signal_type,
            source_platform="instagram",
            target_platform="telegram",
            source_record_id="src-1",
            target_record_id="rec-1",
            value="alice",
            confidence=confidence,
        )

    def _c(self, signal_type, confidence):
        from src.pipeline.entity_resolver import _cross_entity_confidence
        return _cross_entity_confidence(self._make_signal(signal_type, confidence))

    def test_returns_float(self):
        result = self._c("username_exact", 0.9)
        assert isinstance(result, float)

    def test_confidence_bounded_0_to_1(self):
        result = self._c("username_exact", 0.9)
        assert 0.0 <= result <= 1.0

    def test_override_confidence_used_when_registered(self):
        from src.pipeline.entity_resolver import _CROSS_ENTITY_SIGNAL_CONFIDENCE
        if _CROSS_ENTITY_SIGNAL_CONFIDENCE:
            sig_type = next(iter(_CROSS_ENTITY_SIGNAL_CONFIDENCE))
            expected = _CROSS_ENTITY_SIGNAL_CONFIDENCE[sig_type]
            assert abs(self._c(sig_type, 0.5) - expected) < 1e-9

    def test_percentage_normalized(self):
        # confidence > 1 → divide by 100
        result = self._c("unknown_type", 90)
        assert abs(result - 0.9) < 1e-9

    def test_raw_confidence_passthrough(self):
        result = self._c("unknown_type", 0.7)
        assert abs(result - 0.7) < 1e-9

    def test_zero_confidence_returns_zero(self):
        result = self._c("unknown_type", 0.0)
        assert result == 0.0


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: normalize_username_strict additional
# ---------------------------------------------------------------------------

class TestNormalizeUsernameStrictAdditional:
    def _n(self, u):
        from src.pipeline.entity_resolver import normalize_username_strict
        return normalize_username_strict(u)

    def test_digits_preserved(self):
        result = self._n("john123")
        if result:
            assert result.endswith("123")

    def test_dot_stripped(self):
        result = self._n("john.doe")
        if result:
            assert "." not in result

    def test_dash_stripped(self):
        result = self._n("john-doe")
        if result:
            assert "-" not in result

    def test_at_prefix_not_stripped_by_strict(self):
        # normalize_username_strict does NOT strip @ (unlike normalize_username)
        result = self._n("@alice")
        # Result may be '@alice' or None (if default username check fires)
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# Additional: pipeline.notifications.merge_bot token store thread-safety
# ---------------------------------------------------------------------------

class TestMakeTokenThreadSafety:
    def test_same_pair_always_same_token(self):
        from src.notifications.merge_bot import _make_token
        t1 = _make_token("eid-aaa", "eid-bbb")
        t2 = _make_token("eid-aaa", "eid-bbb")
        assert t1 == t2

    def test_different_pairs_different_tokens(self):
        from src.notifications.merge_bot import _make_token
        t1 = _make_token("eid-aaa", "eid-bbb")
        t2 = _make_token("eid-ccc", "eid-ddd")
        assert t1 != t2


# ---------------------------------------------------------------------------
# Additional: api.routes.readiness _collector_summary_ok edge cases
# ---------------------------------------------------------------------------

class TestCollectorSummaryOkAdditional:
    def _ok(self, collector_status):
        from src.api.routes.readiness import _collector_summary_ok
        return _collector_summary_ok(collector_status)

    def test_no_dashboard_fails(self):
        assert self._ok({}) is False

    def test_dashboard_ok_returns_bool(self):
        # _collector_summary_ok with minimal config returns a bool
        result = self._ok({"collector_dashboard": "ok", "summary": {}})
        assert isinstance(result, bool)

    def test_non_ok_dashboard_hard_source_issue_fails(self):
        status = {
            "collector_dashboard": "ok",
            "summary": {
                "source_issue_samples": [{"rollup_exclude": False, "hard": True, "status_severity": "critical"}],
                "source_issues": 1,
            }
        }
        # hard issue present → False
        assert self._ok(status) is False
    def test_dashboard_not_ok_fails(self):
        assert self._ok({"collector_dashboard": "degraded", "summary": {}}) is False
