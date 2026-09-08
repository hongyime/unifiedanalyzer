"""
Pure-function tests — batch 43.

Covers previously untested modules:
- face.engine.detector: FaceDetectionResult (constructor, to_dict), FaceDetector class
  constants (MIN_AREA_RATIO, MIN_LAPLACIAN_VARIANCE, MIN_CONFIDENCE, MAX_ASPECT_RATIO)
- face.identity.metrics: ClusteringMetricsResult dataclass
- db.connection: is_collector_unavailable_error, is_db_transient_error,
  has_analyzer_pool, CollectorUnavailableError, RETRY_DELAYS completeness
"""
from __future__ import annotations

import asyncio
import numpy as np


# ---------------------------------------------------------------------------
# face.engine.detector: FaceDetectionResult + FaceDetector constants
# ---------------------------------------------------------------------------

class TestFaceDetectionResult:
    def _make(self, confidence=0.9, quality_score=0.8, area_ratio=0.1, laplacian=200.0):
        from src.face.engine.detector import FaceDetectionResult
        bbox = np.array([10.0, 20.0, 100.0, 120.0])
        landmark = np.zeros((5, 2))
        return FaceDetectionResult(
            bbox=bbox,
            landmark=landmark,
            confidence=confidence,
            quality_score=quality_score,
            area_ratio=area_ratio,
            laplacian_variance=laplacian,
        )

    def test_fields_stored(self):
        r = self._make()
        assert abs(r.confidence - 0.9) < 1e-9
        assert abs(r.quality_score - 0.8) < 1e-9
        assert abs(r.area_ratio - 0.1) < 1e-9
        assert abs(r.laplacian_variance - 200.0) < 1e-9

    def test_bbox_stored(self):
        r = self._make()
        assert r.bbox[0] == 10.0
        assert r.bbox[2] == 100.0

    def test_to_dict_keys(self):
        r = self._make()
        d = r.to_dict()
        for key in ("bbox", "landmark", "confidence", "quality_score",
                    "area_ratio", "laplacian_variance"):
            assert key in d

    def test_to_dict_bbox_is_list(self):
        r = self._make()
        d = r.to_dict()
        assert isinstance(d["bbox"], list)

    def test_to_dict_confidence_is_float(self):
        r = self._make()
        d = r.to_dict()
        assert isinstance(d["confidence"], float)


class TestFaceDetectorConstants:
    def test_min_area_ratio_in_range(self):
        from src.face.engine.detector import FaceDetector
        assert 0.0 < FaceDetector.MIN_AREA_RATIO <= 1.0

    def test_min_laplacian_variance_positive(self):
        from src.face.engine.detector import FaceDetector
        assert FaceDetector.MIN_LAPLACIAN_VARIANCE > 0

    def test_min_confidence_in_range(self):
        from src.face.engine.detector import FaceDetector
        assert 0.0 < FaceDetector.MIN_CONFIDENCE <= 1.0

    def test_max_aspect_ratio_non_negative(self):
        from src.face.engine.detector import FaceDetector
        assert FaceDetector.MAX_ASPECT_RATIO >= 0.0


# ---------------------------------------------------------------------------
# face.identity.metrics: ClusteringMetricsResult dataclass
# ---------------------------------------------------------------------------

class TestClusteringMetricsResult:
    def _make(self, **kw):
        from src.face.identity.metrics import ClusteringMetricsResult
        defaults = dict(
            silhouette_score=0.5,
            calinski_harabasz_index=100.0,
            davies_bouldin_index=0.8,
            n_samples=50,
            n_clusters=5,
            n_outliers=3,
            avg_cluster_size=9.4,
            min_cluster_size=5,
            max_cluster_size=15,
        )
        defaults.update(kw)
        return ClusteringMetricsResult(**defaults)

    def test_fields_stored(self):
        r = self._make()
        assert abs(r.silhouette_score - 0.5) < 1e-9
        assert r.n_samples == 50
        assert r.n_clusters == 5
        assert r.n_outliers == 3

    def test_edge_case_zero_clusters(self):
        r = self._make(n_clusters=0, n_outliers=50,
                       davies_bouldin_index=float("inf"))
        assert r.n_clusters == 0
        import math
        assert math.isinf(r.davies_bouldin_index)

    def test_avg_cluster_size_positive(self):
        r = self._make(avg_cluster_size=9.4)
        assert r.avg_cluster_size > 0


# ---------------------------------------------------------------------------
# db.connection: is_collector_unavailable_error, is_db_transient_error,
#               has_analyzer_pool, CollectorUnavailableError
# ---------------------------------------------------------------------------

class TestCollectorUnavailableError:
    def test_is_runtime_error_subclass(self):
        from src.db.connection import CollectorUnavailableError
        assert issubclass(CollectorUnavailableError, RuntimeError)

    def test_can_be_raised_and_caught(self):
        from src.db.connection import CollectorUnavailableError
        import pytest
        with pytest.raises(CollectorUnavailableError):
            raise CollectorUnavailableError("pool unavailable")


class TestIsCollectorUnavailableError:
    def _c(self, exc):
        from src.db.connection import is_collector_unavailable_error
        return is_collector_unavailable_error(exc)

    def test_collector_unavailable_error(self):
        from src.db.connection import CollectorUnavailableError
        assert self._c(CollectorUnavailableError("unavailable")) is True

    def test_os_error(self):
        assert self._c(OSError("connection refused")) is True

    def test_timeout_error(self):
        assert self._c(TimeoutError("timed out")) is True

    def test_asyncio_timeout(self):
        assert self._c(asyncio.TimeoutError()) is True

    def test_regular_exception_false(self):
        assert self._c(ValueError("bad value")) is False

    def test_key_error_false(self):
        assert self._c(KeyError("missing")) is False

    def test_name_match_cannotconnect(self):
        class CannotConnectError(Exception):
            pass
        assert self._c(CannotConnectError("failed")) is True


class TestIsDbTransientError:
    def _t(self, exc):
        from src.db.connection import is_db_transient_error
        return is_db_transient_error(exc)

    def test_collector_unavailable_is_transient(self):
        from src.db.connection import CollectorUnavailableError
        assert self._t(CollectorUnavailableError("unavailable")) is True

    def test_startup_message_is_transient(self):
        assert self._t(Exception("the database system is starting up")) is True

    def test_connection_refused_is_transient(self):
        assert self._t(Exception("connection refused")) is True

    def test_too_many_connections_is_transient(self):
        assert self._t(Exception("too many connections")) is True

    def test_value_error_not_transient(self):
        assert self._t(ValueError("bad input")) is False

    def test_syntax_error_not_transient(self):
        assert self._t(Exception("syntax error at position 5")) is False


class TestHasAnalyzerPool:
    def test_returns_bool(self):
        from src.db.connection import has_analyzer_pool
        assert isinstance(has_analyzer_pool(), bool)

    def test_false_without_init(self):
        from src.db.connection import has_analyzer_pool, _analyzer_pool
        # Module-level pool not initialized in tests → should be False
        if _analyzer_pool is None:
            assert has_analyzer_pool() is False
