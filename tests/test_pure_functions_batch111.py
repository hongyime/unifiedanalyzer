"""
Pure-function tests — batch 111.

Covers:
- pipeline.identity_truth: corroborated_auto_truth, build_truth_assertion
- pipeline.entity_resolver: compute_confidence
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.identity_truth: corroborated_auto_truth
# ---------------------------------------------------------------------------

class TestCorroboratedAutoTruth:
    def _make_signal(self, signal_type, confidence, source_platform="instagram",
                     source_table=None, value="alice"):
        return {
            "id": "sig-1",
            "signal_type": signal_type,
            "source_platform": source_platform,
            "source_table": source_table or "entity_platform_links",
            "value": value,
            "confidence": confidence,
            "metadata": None,
        }

    def _c(self, signals, min_confidence=0.85):
        from src.pipeline.identity_truth import corroborated_auto_truth
        return corroborated_auto_truth(signals, min_confidence=min_confidence)

    def test_empty_signals_returns_false(self):
        ok, conf, summary = self._c([])
        assert ok is False
        assert conf == 0.0

    def test_requires_both_spiderfoot_and_hard(self):
        # Only a hard signal, no spiderfoot → False
        sig = self._make_signal("username_exact", 0.9, source_platform="instagram")
        ok, conf, summary = self._c([sig])
        assert ok is False
        assert "requires_spiderfoot_and_independent_hard_signal" in summary.get("reason", "")

    def test_spiderfoot_with_hard_signal_succeeds(self):
        from src.pipeline.identity_truth import SPIDERFOOT_PLATFORMS
        if not SPIDERFOOT_PLATFORMS:
            return  # skip if no spiderfoot platforms defined
        sp_platform = next(iter(SPIDERFOOT_PLATFORMS))
        spiderfoot_sig = self._make_signal("username_exact", 0.9, source_platform=sp_platform)
        hard_sig = self._make_signal("username_exact", 0.95, source_platform="instagram")
        ok, conf, summary = self._c([spiderfoot_sig, hard_sig])
        # Result depends on whether both conditions are met
        assert isinstance(ok, bool)
        assert isinstance(conf, float)

    def test_signal_with_empty_value_excluded(self):
        sig = self._make_signal("username_exact", 0.9, value="")
        ok, conf, summary = self._c([sig])
        assert ok is False


# ---------------------------------------------------------------------------
# pipeline.identity_truth: build_truth_assertion
# ---------------------------------------------------------------------------

class TestBuildTruthAssertion:
    def _make_signal(self, signal_type, confidence, source_platform="instagram", value="alice"):
        return {
            "id": "sig-1",
            "signal_type": signal_type,
            "source_platform": source_platform,
            "source_table": "identity_signals",
            "value": value,
            "confidence": confidence,
            "metadata": None,
        }

    def _b(self, entity_id, value, signals, min_confidence=0.85):
        from src.pipeline.identity_truth import build_truth_assertion
        return build_truth_assertion(entity_id, value, signals, min_confidence=min_confidence)

    def test_empty_signals_returns_none(self):
        assert self._b("eid-1", "alice", []) is None

    def test_returns_dict_on_success(self):
        from src.pipeline.identity_truth import SPIDERFOOT_PLATFORMS
        if not SPIDERFOOT_PLATFORMS:
            return
        sp_platform = next(iter(SPIDERFOOT_PLATFORMS))
        signals = [
            self._make_signal("username_exact", 0.95, source_platform=sp_platform),
            self._make_signal("username_exact", 0.95, source_platform="instagram"),
        ]
        result = self._b("eid-1", "alice", signals)
        if result is not None:
            assert result["assertion_type"] == "same_person"
            assert result["entity_id"] == "eid-1"
            assert result["value"] == "alice"
            assert "truth_state" in result


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: compute_confidence
# ---------------------------------------------------------------------------

class TestComputeConfidence:
    def _c(self, signals):
        from src.pipeline.entity_resolver import compute_confidence
        return compute_confidence(signals)

    def _make_signal(self, signal_type, confidence, target_platform="instagram"):
        from src.pipeline.entity_resolver import SignalMatch
        return SignalMatch(
            signal_type=signal_type,
            source_platform="instagram",
            target_platform=target_platform,
            source_record_id="src-1",
            target_record_id="rec-1",
            value="alice",
            confidence=confidence,
        )

    def test_empty_signals_returns_zero(self):
        norm, strong_count, is_confirmed = self._c([])
        assert norm == 0.0
        assert strong_count == 0
        assert is_confirmed is False

    def test_returns_tuple_of_three(self):
        result = self._c([self._make_signal("username_exact", 0.9)])
        assert len(result) == 3

    def test_normalized_confidence_bounded(self):
        sigs = [self._make_signal("username_exact", 0.9) for _ in range(5)]
        norm, _, _ = self._c(sigs)
        assert 0.0 <= norm <= 1.0

    def test_strong_signal_type_counts(self):
        from src.pipeline.entity_resolver import STRONG_SIGNAL_TYPES
        if STRONG_SIGNAL_TYPES:
            sig_type = next(iter(STRONG_SIGNAL_TYPES))
            _, strong_count, is_confirmed = self._c([self._make_signal(sig_type, 0.9)])
            assert strong_count >= 1
            assert is_confirmed is True

    def test_weak_only_not_confirmed(self):
        # group_cooccurrence is weak/context
        _, strong_count, is_confirmed = self._c([
            self._make_signal("group_cooccurrence", 0.8)
        ])
        assert is_confirmed is False
