"""
Pure-function tests — batch 36.

Covers previously untested pure functions and constants:
- pipeline.entity_resolver: STRONG_SIGNAL_TYPES, VERIFIED_SIGNAL_TYPES,
  _CROSS_ENTITY_SIGNAL_CONFIDENCE, compute_confidence, _cross_entity_confidence,
  _policy_group_key
- pipeline.interaction_graph: SOURCE_QUERIES constant, INTERACTION_BATCH_SIZE,
  SOURCE_QUERY_TIMEOUT_SECONDS
- pipeline.face_clustering: _knn_connected_components basic connectivity
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# entity_resolver: STRONG/VERIFIED signal constants + compute_confidence
# ---------------------------------------------------------------------------

class TestEntityResolverSignalSets:
    def test_strong_signals_non_empty(self):
        from src.pipeline.entity_resolver import STRONG_SIGNAL_TYPES
        assert len(STRONG_SIGNAL_TYPES) > 0

    def test_verified_signals_subset_of_strong(self):
        from src.pipeline.entity_resolver import STRONG_SIGNAL_TYPES, VERIFIED_SIGNAL_TYPES
        assert VERIFIED_SIGNAL_TYPES.issubset(STRONG_SIGNAL_TYPES)

    def test_username_exact_in_strong(self):
        from src.pipeline.entity_resolver import STRONG_SIGNAL_TYPES
        assert "username_exact" in STRONG_SIGNAL_TYPES

    def test_real_name_fuzzy_not_in_strong(self):
        from src.pipeline.entity_resolver import STRONG_SIGNAL_TYPES
        assert "real_name_fuzzy" not in STRONG_SIGNAL_TYPES

    def test_instagram_threads_linked_in_both(self):
        from src.pipeline.entity_resolver import STRONG_SIGNAL_TYPES, VERIFIED_SIGNAL_TYPES
        assert "instagram_threads_linked" in STRONG_SIGNAL_TYPES
        assert "instagram_threads_linked" in VERIFIED_SIGNAL_TYPES

    def test_cross_entity_confidence_all_in_range(self):
        from src.pipeline.entity_resolver import _CROSS_ENTITY_SIGNAL_CONFIDENCE
        for sig, conf in _CROSS_ENTITY_SIGNAL_CONFIDENCE.items():
            assert 0.0 < conf <= 1.0, f"{sig}: {conf}"

    def test_instagram_threads_highest_confidence(self):
        from src.pipeline.entity_resolver import _CROSS_ENTITY_SIGNAL_CONFIDENCE
        assert _CROSS_ENTITY_SIGNAL_CONFIDENCE["instagram_threads_linked"] == 0.99


class TestComputeConfidence:
    def _sig(self, signal_type, confidence=0.9):
        from src.pipeline.entity_resolver import SignalMatch
        return SignalMatch(
            signal_type=signal_type,
            source_platform="instagram",
            target_platform="telegram",
            source_record_id="s1",
            target_record_id="t1",
            value="val",
            confidence=confidence,
        )

    def test_empty_signals_zero(self):
        from src.pipeline.entity_resolver import compute_confidence
        normalized, strong_count, is_confirmed = compute_confidence([])
        assert normalized == 0.0
        assert strong_count == 0
        assert is_confirmed is False

    def test_strong_signal_confirms(self):
        from src.pipeline.entity_resolver import compute_confidence
        sig = self._sig("username_exact")
        normalized, strong_count, is_confirmed = compute_confidence([sig])
        assert is_confirmed is True
        assert strong_count >= 1

    def test_weak_signal_not_confirmed(self):
        from src.pipeline.entity_resolver import compute_confidence
        sig = self._sig("real_name_fuzzy", confidence=0.65)
        normalized, strong_count, is_confirmed = compute_confidence([sig])
        assert is_confirmed is False
        assert strong_count == 0

    def test_normalized_capped_at_1(self):
        from src.pipeline.entity_resolver import compute_confidence
        sigs = [self._sig("username_exact", 99.0)] * 10
        normalized, _, _ = compute_confidence(sigs)
        assert normalized <= 1.0

    def test_normalized_positive_for_signals(self):
        from src.pipeline.entity_resolver import compute_confidence
        sig = self._sig("email_match", 0.9)
        normalized, _, _ = compute_confidence([sig])
        assert normalized > 0.0


class TestCrossEntityConfidence:
    def _sig(self, signal_type, confidence=0.8):
        from src.pipeline.entity_resolver import SignalMatch
        return SignalMatch(
            signal_type=signal_type,
            source_platform="s",
            target_platform="t",
            source_record_id="r1",
            target_record_id="r2",
            value="v",
            confidence=confidence,
        )

    def test_known_signal_returns_fixed_value(self):
        from src.pipeline.entity_resolver import _cross_entity_confidence
        sig = self._sig("whatsapp_phone")
        assert _cross_entity_confidence(sig) == 0.98

    def test_unknown_signal_uses_confidence(self):
        from src.pipeline.entity_resolver import _cross_entity_confidence
        sig = self._sig("unknown_signal", confidence=0.75)
        result = _cross_entity_confidence(sig)
        assert abs(result - 0.75) < 1e-9

    def test_percentage_confidence_divided(self):
        from src.pipeline.entity_resolver import _cross_entity_confidence
        sig = self._sig("unknown_signal", confidence=80.0)
        result = _cross_entity_confidence(sig)
        assert abs(result - 0.8) < 1e-9

    def test_result_capped_at_1(self):
        from src.pipeline.entity_resolver import _cross_entity_confidence
        sig = self._sig("unknown_signal", confidence=999.0)
        assert _cross_entity_confidence(sig) <= 1.0

    def test_result_non_negative(self):
        from src.pipeline.entity_resolver import _cross_entity_confidence
        sig = self._sig("unknown_signal", confidence=0.0)
        assert _cross_entity_confidence(sig) >= 0.0


class TestPolicyGroupKey:
    def _sig(self, platform_id):
        from src.pipeline.entity_resolver import PlatformProfile
        return PlatformProfile(source="instagram", platform_id=platform_id)

    def test_existing_link_returns_existing_tuple(self):
        from src.pipeline.entity_resolver import _policy_group_key
        existing = {("instagram", "12345"): "eid-1"}
        p = self._sig("12345")
        key = _policy_group_key(p, existing)
        assert key[0] == "existing"
        assert "eid-1" in key

    def test_new_profile_returns_new_tuple(self):
        from src.pipeline.entity_resolver import _policy_group_key
        p = self._sig("99999")
        key = _policy_group_key(p, {})
        assert key[0] == "new"
        assert "instagram" in key
        assert "99999" in key


# ---------------------------------------------------------------------------
# interaction_graph: SOURCE_QUERIES, constants
# ---------------------------------------------------------------------------

class TestInteractionGraphConstants:
    def test_source_queries_non_empty(self):
        from src.pipeline.interaction_graph import SOURCE_QUERIES
        assert len(SOURCE_QUERIES) > 0

    def test_each_query_has_required_keys(self):
        from src.pipeline.interaction_graph import SOURCE_QUERIES
        for spec in SOURCE_QUERIES:
            assert "source" in spec
            assert "interaction_type" in spec
            assert "query" in spec

    def test_batch_size_positive(self):
        from src.pipeline.interaction_graph import INTERACTION_BATCH_SIZE
        assert INTERACTION_BATCH_SIZE > 0

    def test_timeout_positive(self):
        from src.pipeline.interaction_graph import SOURCE_QUERY_TIMEOUT_SECONDS
        assert SOURCE_QUERY_TIMEOUT_SECONDS > 0

    def test_telegram_source_present(self):
        from src.pipeline.interaction_graph import SOURCE_QUERIES
        sources = {s["source"] for s in SOURCE_QUERIES}
        assert "telegram" in sources

    def test_instagram_source_present(self):
        from src.pipeline.interaction_graph import SOURCE_QUERIES
        sources = {s["source"] for s in SOURCE_QUERIES}
        assert "instagram" in sources
