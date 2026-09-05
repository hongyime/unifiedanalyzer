"""
QA-lane tests for pure (non-DB) functions in src/pipeline/identity_scorer.py.

Covers:
- _pair_key: deterministic ordering of UUID pairs
- _is_uuid: UUID validation
- _has_identity_evidence: context-only signal filter
- _identity_score_contributions: filters out context-only signals
- _features_from_contributions: max-confidence dedup per signal type
- _feature_snapshot: JSON parsing + float coercion
- _dismissal_suppresses_candidate: hard-signal delta logic
"""
from __future__ import annotations

import json

import pytest

from src.pipeline.identity_scorer import (
    _CONTEXT_ONLY_SIGNALS,
    _DISMISS_RESURFACE_MIN_DELTA,
    _dismissal_suppresses_candidate,
    _feature_snapshot,
    _features_from_contributions,
    _has_identity_evidence,
    _identity_score_contributions,
    _is_uuid,
    _pair_key,
)


# ---------------------------------------------------------------------------
# _pair_key
# ---------------------------------------------------------------------------

class TestPairKey:
    def test_smaller_uuid_first(self):
        a = "00000000-0000-0000-0000-000000000001"
        b = "00000000-0000-0000-0000-000000000002"
        assert _pair_key(a, b) == (a, b)
        assert _pair_key(b, a) == (a, b)

    def test_same_uuid_returns_same_order(self):
        a = "00000000-0000-0000-0000-000000000001"
        assert _pair_key(a, a) == (a, a)

    def test_is_commutative(self):
        a = "aaaaaaaa-0000-0000-0000-000000000000"
        b = "bbbbbbbb-0000-0000-0000-000000000000"
        assert _pair_key(a, b) == _pair_key(b, a)


# ---------------------------------------------------------------------------
# _is_uuid
# ---------------------------------------------------------------------------

class TestIsUuid:
    def test_valid_uuid_returns_true(self):
        assert _is_uuid("00000000-0000-0000-0000-000000000001") is True

    def test_invalid_string_returns_false(self):
        assert _is_uuid("not-a-uuid") is False

    def test_none_returns_false(self):
        assert _is_uuid(None) is False

    def test_empty_string_returns_false(self):
        assert _is_uuid("") is False

    def test_partial_uuid_returns_false(self):
        assert _is_uuid("00000000-0000-0000") is False


# ---------------------------------------------------------------------------
# _has_identity_evidence
# ---------------------------------------------------------------------------

class TestHasIdentityEvidence:
    def test_empty_returns_false(self):
        assert _has_identity_evidence([]) is False

    def test_context_only_signals_return_false(self):
        contributions = [
            ("bio_mention", 0.4),
            ("group_cooccurrence", 0.2),
            ("topical_similarity", 0.15),
        ]
        assert _has_identity_evidence(contributions) is False

    def test_identity_signal_returns_true(self):
        contributions = [("username_exact", 0.7)]
        assert _has_identity_evidence(contributions) is True

    def test_mixed_signals_returns_true(self):
        contributions = [
            ("bio_mention", 0.4),       # context-only
            ("email_match", 0.6),        # identity evidence
        ]
        assert _has_identity_evidence(contributions) is True

    def test_all_context_only_types_accounted_for(self):
        # Every signal in _CONTEXT_ONLY_SIGNALS should cause False when alone
        for sig in _CONTEXT_ONLY_SIGNALS:
            assert _has_identity_evidence([(sig, 0.5)]) is False, (
                f"{sig!r} should be context-only but passed identity evidence check"
            )


# ---------------------------------------------------------------------------
# _identity_score_contributions
# ---------------------------------------------------------------------------

class TestIdentityScoreContributions:
    def test_filters_out_context_only(self):
        contributions = [
            ("username_exact", 0.7),
            ("bio_mention", 0.4),
            ("group_cooccurrence", 0.2),
        ]
        result = _identity_score_contributions(contributions)
        types = [t for t, _ in result]
        assert "username_exact" in types
        assert "bio_mention" not in types
        assert "group_cooccurrence" not in types

    def test_empty_returns_empty(self):
        assert _identity_score_contributions([]) == []

    def test_all_context_only_returns_empty(self):
        contributions = [(sig, 0.5) for sig in _CONTEXT_ONLY_SIGNALS]
        assert _identity_score_contributions(contributions) == []

    def test_all_identity_signals_pass_through(self):
        contributions = [("username_exact", 0.7), ("email_match", 0.6)]
        result = _identity_score_contributions(contributions)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _features_from_contributions
# ---------------------------------------------------------------------------

class TestFeaturesFromContributions:
    def test_empty_returns_empty(self):
        assert _features_from_contributions([]) == {}

    def test_single_signal(self):
        result = _features_from_contributions([("username_exact", 0.7)])
        assert result == {"username_exact": 0.7}

    def test_takes_max_confidence_per_type(self):
        contributions = [
            ("username_exact", 0.5),
            ("username_exact", 0.9),
            ("username_exact", 0.3),
        ]
        result = _features_from_contributions(contributions)
        assert result["username_exact"] == 0.9

    def test_multiple_types_independent(self):
        contributions = [
            ("username_exact", 0.7),
            ("email_match", 0.6),
            ("email_match", 0.8),
        ]
        result = _features_from_contributions(contributions)
        assert result["username_exact"] == 0.7
        assert result["email_match"] == 0.8

    def test_none_confidence_not_inserted(self):
        # float(None or 0.0) == 0.0, and 0.0 > 0.0 is False — key not inserted
        result = _features_from_contributions([("username_exact", None)])
        assert "username_exact" not in result


# ---------------------------------------------------------------------------
# _feature_snapshot
# ---------------------------------------------------------------------------

class TestFeatureSnapshot:
    def test_dict_input_returned_as_floats(self):
        raw = {"username_exact": 0.7, "email_match": 0.6}
        result = _feature_snapshot(raw)
        assert result == {"username_exact": 0.7, "email_match": 0.6}

    def test_json_string_parsed(self):
        raw = json.dumps({"username_exact": 0.7})
        result = _feature_snapshot(raw)
        assert result["username_exact"] == 0.7

    def test_invalid_json_returns_empty(self):
        assert _feature_snapshot("not-json{") == {}

    def test_non_dict_returns_empty(self):
        assert _feature_snapshot([1, 2, 3]) == {}
        assert _feature_snapshot(None) == {}

    def test_non_numeric_values_skipped(self):
        raw = {"username_exact": 0.7, "bad_key": "not_a_float"}
        result = _feature_snapshot(raw)
        assert "username_exact" in result
        assert "bad_key" not in result

    def test_none_value_treated_as_zero(self):
        raw = {"username_exact": None}
        result = _feature_snapshot(raw)
        assert result["username_exact"] == 0.0

    def test_integer_values_coerced_to_float(self):
        raw = {"username_exact": 1}
        result = _feature_snapshot(raw)
        assert result["username_exact"] == 1.0


# ---------------------------------------------------------------------------
# _dismissal_suppresses_candidate
# ---------------------------------------------------------------------------

class TestDismissalSuppressesCandidate:
    def test_empty_dismissed_features_always_suppresses(self):
        # Old dismissals without a feature snapshot stay suppressive
        contributions = [("username_exact", 0.9)]
        assert _dismissal_suppresses_candidate(contributions, {}) is True

    def test_no_hard_signal_growth_keeps_suppressed(self):
        # Only context-only signals in contributions → no reopening
        contributions = [("bio_mention", 0.8), ("group_cooccurrence", 0.9)]
        dismissed = {"bio_mention": 0.5}
        assert _dismissal_suppresses_candidate(contributions, dismissed) is True

    def test_hard_signal_above_threshold_reopens(self):
        # username_exact is in _HARD_SIGNALS
        # Previous dismissed at 0.5, now at 0.5 + _DISMISS_RESURFACE_MIN_DELTA → opens
        new_conf = 0.5 + _DISMISS_RESURFACE_MIN_DELTA
        contributions = [("username_exact", new_conf)]
        dismissed = {"username_exact": 0.5}
        assert _dismissal_suppresses_candidate(contributions, dismissed) is False

    def test_hard_signal_below_threshold_keeps_suppressed(self):
        # New confidence is less than previous + delta
        new_conf = 0.5 + _DISMISS_RESURFACE_MIN_DELTA - 0.01
        contributions = [("username_exact", new_conf)]
        dismissed = {"username_exact": 0.5}
        assert _dismissal_suppresses_candidate(contributions, dismissed) is True

    def test_new_hard_signal_not_in_dismissed_reopens(self):
        # Hard signal wasn't in dismissed snapshot (previous=0) → any confidence ≥ delta opens
        contributions = [("email_match", _DISMISS_RESURFACE_MIN_DELTA)]
        dismissed = {"username_exact": 0.5}  # different signal
        assert _dismissal_suppresses_candidate(contributions, dismissed) is False

    def test_weak_signal_growth_does_not_reopen(self):
        # real_name_fuzzy is NOT in _HARD_SIGNALS
        contributions = [("real_name_fuzzy", 0.99)]
        dismissed = {"real_name_fuzzy": 0.0}
        assert _dismissal_suppresses_candidate(contributions, dismissed) is True
