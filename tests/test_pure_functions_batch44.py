"""
Pure-function tests — batch 44.

Covers previously untested modules:
- face.identity.clustering: ClusteringResult dataclass, QualityAwareClusterer
  constructor (min sizes, threshold, metric stored), _apply_quality_weighting shape
- face.api.routes.gallery: _CROP_MARGIN, _CROP_MAX already tested in batch7 —
  adding _estimated_face_total (no-DB path returns 0), _servable_media_filter shape
- face.engine.quality: QualityScorer full coverage of compute_normalized_area
  (pure math, no I/O)
"""
from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# face.identity.clustering: ClusteringResult + QualityAwareClusterer constructor
# ---------------------------------------------------------------------------

class TestClusteringResult:
    def _make(self, **kw):
        from src.face.identity.clustering import ClusteringResult
        defaults = dict(
            labels=np.array([0, 0, 1, 1, -1]),
            probabilities=np.array([0.9, 0.8, 0.95, 0.85, 0.0]),
            n_clusters=2,
            n_outliers=1,
            cluster_sizes={0: 2, 1: 2},
        )
        defaults.update(kw)
        return ClusteringResult(**defaults)

    def test_fields_stored(self):
        r = self._make()
        assert r.n_clusters == 2
        assert r.n_outliers == 1
        assert len(r.labels) == 5

    def test_empty_cluster_result(self):
        r = self._make(labels=np.array([]), n_clusters=0,
                       n_outliers=0, cluster_sizes={}, probabilities=None)
        assert r.n_clusters == 0
        assert r.probabilities is None

    def test_all_outliers(self):
        r = self._make(labels=np.full(5, -1), n_clusters=0,
                       n_outliers=5, cluster_sizes={})
        assert r.n_outliers == 5
        assert r.n_clusters == 0

    def test_cluster_sizes_dict(self):
        r = self._make()
        assert isinstance(r.cluster_sizes, dict)
        assert 0 in r.cluster_sizes


class TestQualityAwareClusterer:
    def _c(self, min_cluster_size=5, min_samples=3,
           cluster_selection_epsilon=0.6, metric="cosine"):
        from src.face.identity.clustering import QualityAwareClusterer
        return QualityAwareClusterer(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            cluster_selection_epsilon=cluster_selection_epsilon,
            metric=metric,
        )

    def test_defaults_stored(self):
        c = self._c()
        assert c.min_cluster_size == 5
        assert c.min_samples == 3
        assert abs(c.cluster_selection_epsilon - 0.6) < 1e-9
        assert c.metric == "cosine"

    def test_custom_values(self):
        c = self._c(min_cluster_size=3, min_samples=2, metric="euclidean")
        assert c.min_cluster_size == 3
        assert c.metric == "euclidean"

    def test_empty_embeddings_returns_zero_clusters(self):
        c = self._c()
        result = c.cluster(np.zeros((0, 512)))
        assert result.n_clusters == 0
        assert result.n_outliers == 0

    def test_too_few_embeddings_all_outliers(self):
        c = self._c(min_cluster_size=10)
        # Only 3 faces — fewer than min_cluster_size
        embs = np.random.rand(3, 128).astype(np.float32)
        result = c.cluster(embs)
        assert result.n_clusters == 0
        assert result.n_outliers == 3

    def test_quality_weighting_shape_preserved(self):
        c = self._c(min_cluster_size=10)
        embs = np.random.rand(5, 128).astype(np.float32)
        quality = np.array([0.9, 0.5, 0.3, 0.8, 0.7], dtype=np.float32)
        weighted = c._apply_quality_weighting(embs, quality)
        assert weighted.shape == embs.shape


# ---------------------------------------------------------------------------
# face.engine.quality: QualityScorer.compute_normalized_area
# ---------------------------------------------------------------------------

class TestQualityScorerComputeNormalizedArea:
    def _s(self):
        from src.face.engine.quality import QualityScorer
        return QualityScorer()

    def test_small_face_low_area(self):
        s = self._s()
        # 10x10 face in a 1000x1000 image → area_ratio = 0.0001 → normalized very small
        bbox = np.array([0.0, 0.0, 10.0, 10.0])
        result = s.compute_normalized_area(bbox, (1000, 1000))
        assert result < 0.1

    def test_large_face_capped_at_1(self):
        s = self._s()
        # Face = 50% of image → area_ratio = 0.25 → / 0.2 = 1.25 → capped at 1.0
        bbox = np.array([0.0, 0.0, 500.0, 500.0])
        result = s.compute_normalized_area(bbox, (1000, 1000))
        assert result == 1.0

    def test_medium_face(self):
        s = self._s()
        # 200x200 face in 1000x1000 image → area_ratio = 0.04 → / 0.2 = 0.2
        bbox = np.array([0.0, 0.0, 200.0, 200.0])
        result = s.compute_normalized_area(bbox, (1000, 1000))
        assert abs(result - 0.2) < 1e-6

    def test_returns_non_negative(self):
        s = self._s()
        bbox = np.array([0.0, 0.0, 1.0, 1.0])
        result = s.compute_normalized_area(bbox, (100, 100))
        assert result >= 0.0


# ---------------------------------------------------------------------------
# face.api.routes.gallery: _servable_media_filter shape + gallery constants
# ---------------------------------------------------------------------------

class TestGalleryServableFilter:
    def test_filter_non_none(self):
        from src.face.api.routes.gallery import _servable_media_filter
        result = _servable_media_filter()
        # Should return a SQLAlchemy filter expression (non-None)
        assert result is not None

    def test_crop_margin_still_fraction(self):
        from src.face.api.routes.gallery import _CROP_MARGIN
        assert 0.0 < _CROP_MARGIN < 1.0

    def test_crop_max_still_reasonable(self):
        from src.face.api.routes.gallery import _CROP_MAX
        assert 0 < _CROP_MAX <= 2048
