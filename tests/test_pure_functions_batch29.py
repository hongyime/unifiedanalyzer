"""
Pure-function tests — batch 29.

Covers previously untested modules — constants and simple pure functions:
- face_worker: FACE_DB_SCHEMA, _FACE_CONTENT_TYPES, analyzer_sqlalchemy_url (pure URL transform),
  _collector_sqlalchemy_url (pure URL transform)
- pipeline.face_clustering: module-level constants (thresholds, limits, derived methods)
- pipeline.media_analysis_tier1: module-level constants (batch sizes, priority lists)
"""
from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# face_worker: constants + analyzer_sqlalchemy_url
# ---------------------------------------------------------------------------

class TestFaceWorkerConstants:
    def test_face_db_schema_string(self):
        from src.face_worker import FACE_DB_SCHEMA
        assert isinstance(FACE_DB_SCHEMA, str)
        assert len(FACE_DB_SCHEMA) > 0

    def test_face_content_types_non_empty(self):
        from src.face_worker import _FACE_CONTENT_TYPES
        assert len(_FACE_CONTENT_TYPES) > 0
        assert "image" in _FACE_CONTENT_TYPES

    def test_owner_attribution_max_faces_positive(self):
        from src.face_worker import _OWNER_ATTRIBUTION_MAX_FACES
        assert _OWNER_ATTRIBUTION_MAX_FACES >= 1


class TestAnalyzerSqlalchemyUrl:
    def _url(self, raw_url):
        from src.face_worker import analyzer_sqlalchemy_url
        old = os.environ.get("ANALYZER_DATABASE_URL")
        os.environ["ANALYZER_DATABASE_URL"] = raw_url
        try:
            return analyzer_sqlalchemy_url()
        finally:
            if old is None:
                del os.environ["ANALYZER_DATABASE_URL"]
            else:
                os.environ["ANALYZER_DATABASE_URL"] = old

    def test_rewrites_to_psycopg2(self):
        result = self._url("postgres://user:pass@localhost:5432/mydb")
        assert "psycopg2" in result

    def test_localhost_becomes_127(self):
        result = self._url("postgres://user:pass@localhost:5432/mydb")
        assert "127.0.0.1" in result

    def test_non_localhost_preserved(self):
        result = self._url("postgres://user:pass@myhost:5432/mydb")
        assert "myhost" in result

    def test_database_name_preserved(self):
        result = self._url("postgres://user:pass@myhost:5432/unifiedanalyzer")
        assert "unifiedanalyzer" in result

    def test_returns_string(self):
        result = self._url("postgres://u:p@h:5432/db")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# pipeline.face_clustering: module-level constants
# ---------------------------------------------------------------------------

class TestFaceClusteringConstants:
    def test_cluster_threshold_in_range(self):
        from src.pipeline.face_clustering import _CLUSTER_THRESHOLD
        assert 0.0 < _CLUSTER_THRESHOLD <= 1.0

    def test_face_cluster_max_positive(self):
        from src.pipeline.face_clustering import _FACE_CLUSTER_MAX
        assert _FACE_CLUSTER_MAX > 0

    def test_max_propagate_face_count_positive(self):
        from src.pipeline.face_clustering import _MAX_PROPAGATE_FACE_COUNT
        assert _MAX_PROPAGATE_FACE_COUNT >= 1

    def test_propagate_min_sim_in_range(self):
        from src.pipeline.face_clustering import _PROPAGATE_MIN_SIM
        assert 0.0 < _PROPAGATE_MIN_SIM <= 1.0

    def test_unknown_face_count_large(self):
        from src.pipeline.face_clustering import _UNKNOWN_FACE_COUNT
        assert _UNKNOWN_FACE_COUNT > 10  # fail-closed sentinel

    def test_purity_threshold_in_range(self):
        from src.pipeline.face_clustering import _PURITY_2ND_NEAREST_THRESHOLD
        assert 0.0 < _PURITY_2ND_NEAREST_THRESHOLD <= 1.0

    def test_purity_min_tightness_in_range(self):
        from src.pipeline.face_clustering import _PURITY_MIN_TIGHTNESS
        assert 0.0 < _PURITY_MIN_TIGHTNESS <= 1.0

    def test_derived_propagation_methods_non_empty(self):
        from src.pipeline.face_clustering import _DERIVED_PROPAGATION_METHODS
        assert len(_DERIVED_PROPAGATION_METHODS) > 0
        assert "cluster_propagation" in _DERIVED_PROPAGATION_METHODS

    def test_face_knn_positive(self):
        from src.pipeline.face_clustering import _FACE_KNN
        assert _FACE_KNN > 0

    def test_propagate_min_quality_non_negative(self):
        from src.pipeline.face_clustering import _PROPAGATE_MIN_QUALITY
        assert _PROPAGATE_MIN_QUALITY >= 0.0


# ---------------------------------------------------------------------------
# pipeline.media_analysis_tier1: module-level constants
# ---------------------------------------------------------------------------

class TestMediaAnalysisTier1Constants:
    def test_ocr_batch_size_positive(self):
        from src.pipeline.media_analysis_tier1 import MEDIA_OCR_BATCH_SIZE
        assert MEDIA_OCR_BATCH_SIZE > 0

    def test_face_batch_size_positive(self):
        from src.pipeline.media_analysis_tier1 import MEDIA_FACE_BATCH_SIZE
        assert MEDIA_FACE_BATCH_SIZE > 0

    def test_video_frame_batch_size_positive(self):
        from src.pipeline.media_analysis_tier1 import MEDIA_VIDEO_FRAME_BATCH_SIZE
        assert MEDIA_VIDEO_FRAME_BATCH_SIZE > 0

    def test_face_match_max_positive(self):
        from src.pipeline.media_analysis_tier1 import MEDIA_FACE_MATCH_MAX
        assert MEDIA_FACE_MATCH_MAX > 0

    def test_ocr_priority_1_non_empty(self):
        from src.pipeline.media_analysis_tier1 import _OCR_PRIORITY_1
        assert len(_OCR_PRIORITY_1) > 0

    def test_ocr_enabled_is_bool(self):
        from src.pipeline.media_analysis_tier1 import MEDIA_OCR_ENABLED
        assert isinstance(MEDIA_OCR_ENABLED, bool)

    def test_drive_available_callable(self):
        from src.pipeline.media_analysis_tier1 import _drive_available
        # Should be callable and return bool without crashing
        result = _drive_available()
        assert isinstance(result, bool)
