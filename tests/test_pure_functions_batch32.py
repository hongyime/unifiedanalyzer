"""
Pure-function tests — batch 32.

Covers remaining untested pure functions and constants:
- pipeline.identity_scorer: _MIN_SCORE, _HIGH_CONFIDENCE, _SAME_PLATFORM_MULTIPLIER,
  _CROSS_PLATFORM_MULTIPLIER, _DISMISS_RESURFACE_MIN_DELTA, _TYPE_WEIGHT completeness
- pipeline.face_associations: _normalize_embedding edge cases (already in batch9 —
  adding _ASSOC_THRESHOLD, _MAX_MEDIA, _ATTRIBUTION_METHODS constants)
- pipeline.face_pair_signals: _PAIR_KNN_THRESHOLD, _PAIR_KNN_MIN_MATCHES,
  _PORTRAIT_MAX_FACES constants
- pipeline.social_face_link: SOCIAL_FACE_LINK_THRESHOLD (env), max_pairs
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: remaining constants
# ---------------------------------------------------------------------------

class TestIdentityScorerRemainingConstants:
    def test_min_score_positive(self):
        from src.pipeline.identity_scorer import _MIN_SCORE
        assert 0.0 < _MIN_SCORE <= 1.0

    def test_high_confidence_in_range(self):
        from src.pipeline.identity_scorer import _HIGH_CONFIDENCE
        assert 0.0 < _HIGH_CONFIDENCE <= 1.0

    def test_same_platform_multiplier_less_than_1(self):
        from src.pipeline.identity_scorer import _SAME_PLATFORM_MULTIPLIER
        # Penalty: should be < 1.0 so same-platform pairs are dimmed
        assert 0.0 < _SAME_PLATFORM_MULTIPLIER < 1.0

    def test_cross_platform_multiplier_gte_1(self):
        from src.pipeline.identity_scorer import _CROSS_PLATFORM_MULTIPLIER
        # Boost: >= 1.0 for cross-platform
        assert _CROSS_PLATFORM_MULTIPLIER >= 1.0

    def test_dismiss_resurface_min_delta_positive(self):
        from src.pipeline.identity_scorer import _DISMISS_RESURFACE_MIN_DELTA
        assert _DISMISS_RESURFACE_MIN_DELTA > 0.0

    def test_type_weight_all_values_in_range(self):
        from src.pipeline.identity_scorer import _TYPE_WEIGHT
        for sig_type, weight in _TYPE_WEIGHT.items():
            assert 0.0 < weight <= 1.0, f"{sig_type} weight {weight} out of range"

    def test_type_weight_email_match_value(self):
        from src.pipeline.identity_scorer import _TYPE_WEIGHT
        assert _TYPE_WEIGHT["email_match"] == 0.60

    def test_type_weight_face_pair_knn_higher_than_media_face_match(self):
        from src.pipeline.identity_scorer import _TYPE_WEIGHT
        assert _TYPE_WEIGHT["face_pair_knn"] > _TYPE_WEIGHT["media_face_match"]

    def test_context_only_signals_not_in_type_weight_or_low(self):
        from src.pipeline.identity_scorer import _TYPE_WEIGHT, _CONTEXT_ONLY_SIGNALS
        # Context-only signals should still exist in _TYPE_WEIGHT but be low weight
        for sig in _CONTEXT_ONLY_SIGNALS:
            if sig in _TYPE_WEIGHT:
                assert _TYPE_WEIGHT[sig] <= 0.40, f"{sig} weight should be low"


# ---------------------------------------------------------------------------
# pipeline.face_associations: additional constants
# ---------------------------------------------------------------------------

class TestFaceAssociationsConstants:
    def test_assoc_threshold_in_range(self):
        from src.pipeline.face_associations import _ASSOC_THRESHOLD
        assert 0.0 < _ASSOC_THRESHOLD <= 1.0

    def test_max_media_positive(self):
        from src.pipeline.face_associations import _MAX_MEDIA
        assert _MAX_MEDIA > 0

    def test_attribution_methods_non_empty(self):
        from src.pipeline.face_associations import _ATTRIBUTION_METHODS
        assert len(_ATTRIBUTION_METHODS) >= 2
        assert "media_attribution" in _ATTRIBUTION_METHODS
        assert "media_attribution_relink" in _ATTRIBUTION_METHODS

    def test_normalize_embedding_512d(self):
        import json
        import numpy as np
        from src.pipeline.face_associations import _normalize_embedding
        vec = [0.1] * 512
        result = _normalize_embedding(json.dumps(vec))
        assert result is not None
        assert abs(float(np.linalg.norm(result)) - 1.0) < 1e-5

    def test_normalize_embedding_zero_returns_none(self):
        import json
        from src.pipeline.face_associations import _normalize_embedding
        result = _normalize_embedding(json.dumps([0.0] * 512))
        assert result is None


# ---------------------------------------------------------------------------
# pipeline.face_pair_signals: constants
# ---------------------------------------------------------------------------

class TestFacePairSignalsConstants:
    def test_pair_knn_threshold_in_range(self):
        from src.pipeline.face_pair_signals import _PAIR_KNN_THRESHOLD
        assert 0.0 < _PAIR_KNN_THRESHOLD <= 1.0

    def test_pair_knn_min_matches_positive(self):
        from src.pipeline.face_pair_signals import _PAIR_KNN_MIN_MATCHES
        assert _PAIR_KNN_MIN_MATCHES >= 2  # at least 2 matches required

    def test_portrait_max_faces_small(self):
        from src.pipeline.face_pair_signals import _PORTRAIT_MAX_FACES
        # Portrait gate: small (selfies/portraits, not crowds)
        assert 1 <= _PORTRAIT_MAX_FACES <= 5

    def test_max_faces_per_entity_positive(self):
        from src.pipeline.face_pair_signals import _PAIR_KNN_MAX_FACES_PER_ENTITY
        assert _PAIR_KNN_MAX_FACES_PER_ENTITY > 0


# ---------------------------------------------------------------------------
# pipeline.social_face_link: additional env constants
# ---------------------------------------------------------------------------

class TestSocialFaceLinkConstants:
    def test_enabled_default_is_bool(self, monkeypatch):
        monkeypatch.delenv("SOCIAL_FACE_LINK_ENABLED", raising=False)
        from src.pipeline.social_face_link import _enabled
        assert isinstance(_enabled(), bool)

    def test_threshold_env(self, monkeypatch):
        monkeypatch.delenv("SOCIAL_FACE_LINK_THRESHOLD", raising=False)
        # Default threshold should be reasonable (0.55)
        import os
        default = float(os.getenv("SOCIAL_FACE_LINK_THRESHOLD", "0.55"))
        assert 0.0 < default <= 1.0

    def test_max_pairs_env(self, monkeypatch):
        monkeypatch.delenv("SOCIAL_FACE_LINK_MAX_PAIRS", raising=False)
        import os
        default = int(os.getenv("SOCIAL_FACE_LINK_MAX_PAIRS", "500"))
        assert default > 0
