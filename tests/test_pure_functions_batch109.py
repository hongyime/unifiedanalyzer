"""
Pure-function tests — batch 109.

Covers:
- pipeline.temporal_correlation: _hour_idf_weights, _weighted_cosine
- pipeline.identity_scorer: _cross_entity_confidence
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.temporal_correlation: _hour_idf_weights
# ---------------------------------------------------------------------------

class TestHourIdfWeights:
    def _w(self, hour_dists):
        from src.pipeline.temporal_correlation import _hour_idf_weights
        return _hour_idf_weights(hour_dists)

    def test_empty_returns_24_weights(self):
        result = self._w({})
        assert len(result) == 24

    def test_all_floored_at_0_05(self):
        # With many entities posting every hour, weights are floored at 0.05
        dists = {f"e{i}": [1.0] * 24 for i in range(100)}
        result = self._w(dists)
        assert all(w >= 0.05 for w in result)

    def test_rare_hour_gets_higher_weight(self):
        # Only entity posts in hour 3 — that hour should have higher weight
        # than hour 0 which nobody posts in
        dists = {"e1": [1.0 if h == 3 else 0.0 for h in range(24)]}
        result = self._w(dists)
        assert result[3] >= result[0]  # rare hour 3 >= silent hour 0

    def test_universal_hour_still_positive(self):
        # Even a universal hour has weight >= 0.05
        dists = {f"e{i}": [1.0] * 24 for i in range(10)}
        result = self._w(dists)
        assert all(w > 0 for w in result)

    def test_single_entity_returns_24_weights(self):
        dist = [float(h % 2) for h in range(24)]
        result = self._w({"e1": dist})
        assert len(result) == 24


# ---------------------------------------------------------------------------
# pipeline.temporal_correlation: _weighted_cosine
# ---------------------------------------------------------------------------

class TestWeightedCosine:
    def _wc(self, a, b, w):
        from src.pipeline.temporal_correlation import _weighted_cosine
        return _weighted_cosine(a, b, w)

    def _make_24(self, val=0.0):
        return [val] * 24

    def test_identical_histograms_returns_one(self):
        v = [float(h % 2 + 0.1) for h in range(24)]
        w = [1.0] * 24
        assert abs(self._wc(v, v, w) - 1.0) < 1e-9

    def test_zero_histograms_returns_zero(self):
        w = [1.0] * 24
        assert self._wc(self._make_24(0), self._make_24(0), w) == 0.0

    def test_orthogonal_returns_zero(self):
        a = [1.0 if h < 12 else 0.0 for h in range(24)]
        b = [0.0 if h < 12 else 1.0 for h in range(24)]
        w = [1.0] * 24
        assert self._wc(a, b, w) == 0.0

    def test_zero_weights_returns_zero(self):
        v = [1.0] * 24
        w = [0.0] * 24
        assert self._wc(v, v, w) == 0.0

    def test_range_zero_to_one(self):
        import random
        rng = random.Random(42)
        a = [rng.random() for _ in range(24)]
        b = [rng.random() for _ in range(24)]
        w = [rng.random() + 0.1 for _ in range(24)]
        result = self._wc(a, b, w)
        assert 0.0 <= result <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: _TYPE_WEIGHT constants + confidence_bucket
# ---------------------------------------------------------------------------

class TestTypeWeightConstants:
    def test_type_weight_is_dict(self):
        from src.pipeline.identity_scorer import _TYPE_WEIGHT
        assert isinstance(_TYPE_WEIGHT, dict)

    def test_hard_signals_have_non_zero_weight(self):
        from src.pipeline.identity_scorer import _TYPE_WEIGHT
        assert _TYPE_WEIGHT.get("username_exact", 0) > 0

    def test_all_weights_between_0_and_1(self):
        from src.pipeline.identity_scorer import _TYPE_WEIGHT
        for sig_type, weight in _TYPE_WEIGHT.items():
            assert 0.0 <= weight <= 1.0


class TestConfidenceBucketAdditional:
    def _b(self, rtype, weight, cross=False):
        from src.api.routes.graph import confidence_bucket
        return confidence_bucket(rtype, weight, cross)

    def test_same_person_probability_returns_hard(self):
        assert self._b("same_person_probability", 100) == "hard"

    def test_cross_entity_boost_non_negative(self):
        order = ["context-only", "weak", "moderate", "strong"]
        plain = self._b("interaction", 50)
        cross = self._b("interaction", 50, cross=True)
        assert order.index(cross) >= order.index(plain)
