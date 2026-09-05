"""
QA-lane tests for pure functions in src/pipeline/identity_calibration.py.

Covers:
- pair_feature_vector: fixed-length feature vector from (signal_type, confidence) pairs
- _feature_value: single-value lookup with deprecated-type guard
- _jsonload: dict/str/invalid JSON coercion
- _noisy_or_probs: noisy-OR probability computation
"""
from __future__ import annotations

import pytest

from src.pipeline.identity_calibration import (
    DEPRECATED_NON_IDENTITY_FEATURES,
    FEATURE_ORDER,
    _feature_value,
    _jsonload,
    _noisy_or_probs,
    pair_feature_vector,
)


# ---------------------------------------------------------------------------
# pair_feature_vector
# ---------------------------------------------------------------------------

class TestPairFeatureVector:
    def test_returns_fixed_length(self):
        result = pair_feature_vector([])
        assert len(result) == len(FEATURE_ORDER)

    def test_all_zeros_when_empty(self):
        result = pair_feature_vector([])
        assert all(v == 0.0 for v in result)

    def test_known_signal_type_fills_correct_slot(self):
        # Pick the first non-deprecated signal type in FEATURE_ORDER
        sig = next(t for t in FEATURE_ORDER if t not in DEPRECATED_NON_IDENTITY_FEATURES)
        result = pair_feature_vector([(sig, 0.9)])
        idx = FEATURE_ORDER.index(sig)
        assert abs(result[idx] - 0.9) < 1e-9

    def test_takes_max_confidence_per_type(self):
        sig = next(t for t in FEATURE_ORDER if t not in DEPRECATED_NON_IDENTITY_FEATURES)
        result = pair_feature_vector([(sig, 0.5), (sig, 0.9), (sig, 0.3)])
        idx = FEATURE_ORDER.index(sig)
        assert abs(result[idx] - 0.9) < 1e-9

    def test_deprecated_signals_zero_out(self):
        if not DEPRECATED_NON_IDENTITY_FEATURES:
            pytest.skip("No deprecated features defined")
        dep = next(iter(DEPRECATED_NON_IDENTITY_FEATURES))
        if dep not in FEATURE_ORDER:
            pytest.skip(f"{dep} not in FEATURE_ORDER")
        result = pair_feature_vector([(dep, 0.9)])
        idx = FEATURE_ORDER.index(dep)
        assert result[idx] == 0.0


# ---------------------------------------------------------------------------
# _feature_value
# ---------------------------------------------------------------------------

class TestFeatureValue:
    def test_present_key_returned(self):
        sig = next(t for t in FEATURE_ORDER if t not in DEPRECATED_NON_IDENTITY_FEATURES)
        assert abs(_feature_value({sig: 0.7}, sig) - 0.7) < 1e-9

    def test_missing_key_returns_zero(self):
        assert _feature_value({}, "email_match") == 0.0

    def test_deprecated_type_returns_zero(self):
        if not DEPRECATED_NON_IDENTITY_FEATURES:
            pytest.skip("No deprecated features defined")
        dep = next(iter(DEPRECATED_NON_IDENTITY_FEATURES))
        assert _feature_value({dep: 0.9}, dep) == 0.0


# ---------------------------------------------------------------------------
# _jsonload
# ---------------------------------------------------------------------------

class TestJsonload:
    def test_dict_passthrough(self):
        assert _jsonload({"a": 1}) == {"a": 1}

    def test_valid_json_string_parsed(self):
        assert _jsonload('{"x": 2}') == {"x": 2}

    def test_json_array_returns_empty(self):
        assert _jsonload("[1, 2]") == {}

    def test_invalid_json_returns_empty(self):
        assert _jsonload("bad{") == {}

    def test_none_returns_empty(self):
        assert _jsonload(None) == {}


# ---------------------------------------------------------------------------
# _noisy_or_probs
# ---------------------------------------------------------------------------

class TestNoisyOrProbs:
    def test_all_zero_features_give_zero(self):
        X = [[0.0] * len(FEATURE_ORDER)]
        result = _noisy_or_probs(X)
        assert len(result) == 1
        assert result[0] == 0.0

    def test_nonzero_feature_gives_positive_prob(self):
        # Set a known signal type with high confidence
        sig = next(t for t in FEATURE_ORDER if t not in DEPRECATED_NON_IDENTITY_FEATURES)
        idx = FEATURE_ORDER.index(sig)
        row = [0.0] * len(FEATURE_ORDER)
        row[idx] = 0.9
        result = _noisy_or_probs([row])
        assert result[0] > 0.0

    def test_probability_capped_below_1(self):
        # All features at max → very high but < 1
        X = [[1.0] * len(FEATURE_ORDER)]
        result = _noisy_or_probs(X)
        assert result[0] < 1.0

    def test_multiple_rows_returned(self):
        X = [[0.0] * len(FEATURE_ORDER)] * 5
        result = _noisy_or_probs(X)
        assert len(result) == 5

    def test_higher_confidence_gives_higher_prob(self):
        sig = next(t for t in FEATURE_ORDER if t not in DEPRECATED_NON_IDENTITY_FEATURES)
        idx = FEATURE_ORDER.index(sig)
        low = [0.0] * len(FEATURE_ORDER)
        high = [0.0] * len(FEATURE_ORDER)
        low[idx] = 0.3
        high[idx] = 0.9
        r_low = _noisy_or_probs([low])[0]
        r_high = _noisy_or_probs([high])[0]
        assert r_high > r_low
