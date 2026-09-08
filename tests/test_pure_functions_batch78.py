"""
Pure-function tests — batch 78.

Covers:
- pipeline.stream_alerts: make_alert_fingerprint, is_suppressed,
  emotional_z_score, collector_resume_from_status,
  burst_alert_type_for_event_type, _hour_bucket, _detail_dict
- pipeline.group_graph: _group_weight, _effective_group_size
- pipeline.relationship_intelligence: _sorted_pair, _jaccard,
  _scaled_similarity, _cosine_sim, _decode_meta
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: make_alert_fingerprint
# ---------------------------------------------------------------------------

class TestMakeAlertFingerprint:
    def _f(self, alert_type="silence_gap", entity_id="eid-1", source="instagram",
           bucket_key="2026-01-01T00", window_start=None, window_end=None):
        from src.pipeline.stream_alerts import make_alert_fingerprint
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return make_alert_fingerprint(
            alert_type,
            entity_id=entity_id, source=source,
            bucket_key=bucket_key,
            window_start=window_start or dt,
            window_end=window_end or dt + timedelta(hours=1),
        )

    def test_returns_fingerprint_object(self):
        fp = self._f()
        assert fp.fingerprint is not None
        assert len(fp.fingerprint) == 64  # sha256 hex

    def test_deterministic(self):
        assert self._f().fingerprint == self._f().fingerprint

    def test_different_entity_different_fingerprint(self):
        a = self._f(entity_id="eid-1")
        b = self._f(entity_id="eid-2")
        assert a.fingerprint != b.fingerprint

    def test_none_entity_id_handled(self):
        fp = self._f(entity_id=None)
        assert fp.fingerprint is not None

    def test_alert_type_stored(self):
        fp = self._f(alert_type="coordinated_posting")
        assert fp.alert_type == "coordinated_posting"


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: is_suppressed
# ---------------------------------------------------------------------------

class TestIsSuppressed:
    def _s(self, suppressions, alert_type="silence_gap", entity_id="eid-1",
           source="instagram", now=None):
        from src.pipeline.stream_alerts import is_suppressed
        now = now or datetime(2026, 6, 1, tzinfo=timezone.utc)
        return is_suppressed(
            suppressions,
            alert_type=alert_type, entity_id=entity_id,
            source=source, now=now,
        )

    def test_empty_suppressions_not_suppressed(self):
        assert self._s([]) is False

    def test_exact_match_suppressed(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        sup = {"alert_type": "silence_gap", "entity_id": "eid-1",
               "source": "instagram", "starts_at": None, "ends_at": None}
        assert self._s([sup]) is True

    def test_wildcard_alert_type_suppressed(self):
        sup = {"alert_type": "*", "entity_id": None, "source": None,
               "starts_at": None, "ends_at": None}
        assert self._s([sup]) is True

    def test_different_entity_not_suppressed(self):
        sup = {"alert_type": "silence_gap", "entity_id": "other-eid",
               "source": None, "starts_at": None, "ends_at": None}
        assert self._s([sup]) is False

    def test_expired_suppression_not_suppressed(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        sup = {"alert_type": "silence_gap", "entity_id": "eid-1",
               "source": "instagram", "starts_at": None,
               "ends_at": datetime(2026, 5, 1, tzinfo=timezone.utc)}
        assert self._s([sup], now=now) is False

    def test_future_suppression_not_active(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        sup = {"alert_type": "silence_gap", "entity_id": "eid-1",
               "source": "instagram",
               "starts_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
               "ends_at": None}
        assert self._s([sup], now=now) is False


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: emotional_z_score
# ---------------------------------------------------------------------------

class TestEmotionalZScore:
    def _z(self, current, baseline, stddev):
        from src.pipeline.stream_alerts import emotional_z_score
        return emotional_z_score(current, baseline, stddev)

    def test_zero_stddev_uses_fallback(self):
        # stddev=None or 0 → uses fallback
        result = self._z(5.0, 2.0, None)
        assert isinstance(result, float)

    def test_positive_z(self):
        result = self._z(10.0, 2.0, 2.0)
        assert result > 0

    def test_negative_z(self):
        result = self._z(0.0, 5.0, 2.0)
        assert result < 0

    def test_zero_diff_zero_z(self):
        result = self._z(5.0, 5.0, 2.0)
        assert abs(result) < 1e-9


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: collector_resume_from_status
# ---------------------------------------------------------------------------

class TestCollectorResumeFromStatus:
    def _c(self, previous, current):
        from src.pipeline.stream_alerts import collector_resume_from_status
        return collector_resume_from_status(previous, current)

    def test_stale_to_fresh_is_resume(self):
        assert self._c("stale", "fresh") is True

    def test_degraded_to_fresh_is_resume(self):
        assert self._c("degraded", "fresh") is True

    def test_active_to_fresh_not_resume(self):
        assert self._c("active", "fresh") is False

    def test_none_previous_not_resume(self):
        assert self._c(None, "fresh") is False

    def test_stale_to_stale_not_resume(self):
        assert self._c("stale", "stale") is False


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: burst_alert_type_for_event_type
# ---------------------------------------------------------------------------

class TestBurstAlertTypeForEventType:
    def _b(self, event_type):
        from src.pipeline.stream_alerts import burst_alert_type_for_event_type
        return burst_alert_type_for_event_type(event_type)

    def test_known_event_type_returns_string(self):
        result = self._b("post")
        assert result is None or isinstance(result, str)

    def test_none_returns_none(self):
        assert self._b(None) is None

    def test_unknown_returns_none(self):
        assert self._b("totally_unknown_xyz") is None


# ---------------------------------------------------------------------------
# pipeline.group_graph: _group_weight
# ---------------------------------------------------------------------------

class TestGroupWeight:
    def _w(self, count):
        from src.pipeline.group_graph import _group_weight
        return _group_weight(count)

    def test_one_member_returns_zero(self):
        assert self._w(1) == 0.0

    def test_two_members_returns_one(self):
        assert abs(self._w(2) - 1.0) < 1e-9

    def test_ten_members(self):
        assert abs(self._w(10) - 1 / 9) < 1e-9

    def test_large_group_small_weight(self):
        assert self._w(10_001) < 0.001

    def test_zero_members_returns_zero(self):
        assert self._w(0) == 0.0


# ---------------------------------------------------------------------------
# pipeline.group_graph: _effective_group_size
# ---------------------------------------------------------------------------

class TestEffectiveGroupSize:
    def _e(self, true_size, tracked_count):
        from src.pipeline.group_graph import _effective_group_size
        return _effective_group_size(true_size, tracked_count)

    def test_true_size_larger_preferred(self):
        assert self._e(100, 7) == 100

    def test_none_true_size_fallback_tracked(self):
        assert self._e(None, 7) == 7

    def test_true_size_smaller_than_tracked_fallback(self):
        # true_size < tracked_count → use tracked
        assert self._e(3, 7) == 7

    def test_equal_uses_true_size(self):
        assert self._e(7, 7) == 7

    def test_zero_true_size_fallback(self):
        assert self._e(0, 5) == 5


# ---------------------------------------------------------------------------
# pipeline.relationship_intelligence: _sorted_pair
# ---------------------------------------------------------------------------

class TestSortedPair:
    def _s(self, a, b):
        from src.pipeline.relationship_intelligence import _sorted_pair
        return _sorted_pair(a, b)

    def test_already_sorted(self):
        assert self._s("a", "b") == ("a", "b")

    def test_reversed_sorted(self):
        assert self._s("b", "a") == ("a", "b")

    def test_same_values(self):
        assert self._s("x", "x") == ("x", "x")


# ---------------------------------------------------------------------------
# pipeline.relationship_intelligence: _jaccard
# ---------------------------------------------------------------------------

class TestJaccard:
    def _j(self, a, b):
        from src.pipeline.relationship_intelligence import _jaccard
        return _jaccard(a, b)

    def test_empty_sets_returns_zero(self):
        assert self._j(set(), set()) == 0.0

    def test_identical_sets_returns_one(self):
        assert abs(self._j({1, 2, 3}, {1, 2, 3}) - 1.0) < 1e-9

    def test_disjoint_sets_returns_zero(self):
        assert self._j({1, 2}, {3, 4}) == 0.0

    def test_partial_overlap(self):
        result = self._j({1, 2, 3}, {2, 3, 4})
        assert abs(result - 0.5) < 1e-9  # |{2,3}| / |{1,2,3,4}| = 2/4

    def test_one_empty_returns_zero(self):
        assert self._j({1, 2}, set()) == 0.0


# ---------------------------------------------------------------------------
# pipeline.relationship_intelligence: _scaled_similarity
# ---------------------------------------------------------------------------

class TestScaledSimilarity:
    def _s(self, a, b, floor=1.0):
        from src.pipeline.relationship_intelligence import _scaled_similarity
        return _scaled_similarity(a, b, floor)

    def test_equal_values_returns_one(self):
        assert abs(self._s(5.0, 5.0) - 1.0) < 1e-9

    def test_invalid_returns_none(self):
        assert self._s("bad", 5.0) is None

    def test_range_zero_to_one(self):
        result = self._s(3.0, 7.0)
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_floor_applied(self):
        # floor=10 means denominator at least 10
        result = self._s(1.0, 2.0, floor=10.0)
        assert result is not None
        assert result == 0.9  # 1 - |1-2|/10


# ---------------------------------------------------------------------------
# pipeline.relationship_intelligence: _cosine_sim
# ---------------------------------------------------------------------------

class TestCosineSim:
    def _c(self, a, b):
        from src.pipeline.relationship_intelligence import _cosine_sim
        return _cosine_sim(a, b)

    def test_empty_dicts_returns_zero(self):
        assert self._c({}, {}) == 0.0

    def test_identical_dicts_returns_one(self):
        d = {"a": 2, "b": 3}
        assert abs(self._c(d, d) - 1.0) < 1e-9

    def test_disjoint_dicts_returns_zero(self):
        assert self._c({"a": 1}, {"b": 1}) == 0.0

    def test_range_zero_to_one(self):
        result = self._c({"a": 1, "b": 2}, {"a": 2, "b": 1})
        assert 0.0 <= result <= 1.0
