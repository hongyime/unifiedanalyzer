"""
Pure-function tests — batch 31.

Covers previously untested pure functions and constants:
- pipeline.identity_calibration: FEATURE_ORDER length, ACTIVE_FEATURE_ORDER properties,
  DEPRECATED_NON_IDENTITY_FEATURES membership (already covered in batch 15 partially —
  adding _feature_value edge cases and snapshot_pair_features stub check)
- pipeline.face_clustering: _JUNK_MIN_CONFIDENCE, _JUNK_MAX_ASPECT, _DERIVED_PROPAGATION_METHODS
  (already covered batch 29 partly — adding _DRIVE_XREF constants + _last_face_count)
- pipeline.media_analysis_tier1: _OCR_MAX_DIM, _MAX_OCR_TEXT_LEN, _OCR_PRIORITY_2
- pipeline.media_analysis: _EXIF_MAKE/MODEL/BODY_SERIAL/LENS constants, _GPS_CLUSTER_PRECISION,
  _GPS_TIME_WINDOW, MEDIA_EXIF_BATCH_SIZE/MEDIA_PHASH_BATCH_SIZE
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.identity_calibration: additional constant checks
# ---------------------------------------------------------------------------

class TestIdentityCalibrationAdditional:
    def test_feature_order_length_at_least_14(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        assert len(FEATURE_ORDER) >= 14

    def test_active_feature_order_smaller_than_full(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER, ACTIVE_FEATURE_ORDER
        assert len(ACTIVE_FEATURE_ORDER) < len(FEATURE_ORDER)

    def test_feature_order_has_no_duplicates(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        assert len(FEATURE_ORDER) == len(set(FEATURE_ORDER))

    def test_active_order_has_no_duplicates(self):
        from src.pipeline.identity_calibration import ACTIVE_FEATURE_ORDER
        assert len(ACTIVE_FEATURE_ORDER) == len(set(ACTIVE_FEATURE_ORDER))

    def test_feature_value_deprecated_always_zero(self):
        from src.pipeline.identity_calibration import _feature_value, DEPRECATED_NON_IDENTITY_FEATURES
        for feat in DEPRECATED_NON_IDENTITY_FEATURES:
            assert _feature_value({feat: 0.99}, feat) == 0.0

    def test_feature_value_active_returns_value(self):
        from src.pipeline.identity_calibration import _feature_value
        assert abs(_feature_value({"email_match": 0.85}, "email_match") - 0.85) < 1e-9

    def test_model_path_is_string(self):
        from src.pipeline.identity_calibration import _MODEL_PATH
        assert isinstance(_MODEL_PATH, str)

    def test_auto_positive_source_non_empty(self):
        # Verify the constant exists in auto_labeler
        from src.pipeline.auto_labeler import _AUTO_POSITIVE_SOURCE
        assert isinstance(_AUTO_POSITIVE_SOURCE, str)
        assert len(_AUTO_POSITIVE_SOURCE) > 0


# ---------------------------------------------------------------------------
# pipeline.face_clustering: additional junk-gate constants
# ---------------------------------------------------------------------------

class TestFaceClusteringJunkConstants:
    def test_junk_min_confidence_in_range(self):
        from src.pipeline.face_clustering import _JUNK_MIN_CONFIDENCE
        assert 0.0 <= _JUNK_MIN_CONFIDENCE <= 1.0

    def test_junk_max_aspect_positive(self):
        from src.pipeline.face_clustering import _JUNK_MAX_ASPECT
        assert _JUNK_MAX_ASPECT > 1.0  # aspect > 1 needed for portrait guard

    def test_junk_min_area_non_negative(self):
        from src.pipeline.face_clustering import _JUNK_MIN_AREA_PERCENT
        assert _JUNK_MIN_AREA_PERCENT >= 0.0

    def test_junk_min_laplacian_non_negative(self):
        from src.pipeline.face_clustering import _JUNK_MIN_LAPLACIAN
        assert _JUNK_MIN_LAPLACIAN >= 0.0

    def test_drive_xref_threshold_in_range(self):
        from src.pipeline.face_clustering import _DRIVE_XREF_THRESHOLD
        assert 0.0 < _DRIVE_XREF_THRESHOLD <= 1.0

    def test_drive_xref_top_margin_positive(self):
        from src.pipeline.face_clustering import _DRIVE_XREF_TOP_MARGIN
        assert _DRIVE_XREF_TOP_MARGIN > 0.0

    def test_drive_xref_batch_positive(self):
        from src.pipeline.face_clustering import _DRIVE_XREF_BATCH
        assert _DRIVE_XREF_BATCH > 0

    def test_knn_propagation_threshold_in_range(self):
        from src.pipeline.face_clustering import _FACE_KNN_PROPAGATION_THRESHOLD
        assert 0.0 < _FACE_KNN_PROPAGATION_THRESHOLD <= 1.0

    def test_exact_search_max_positive(self):
        from src.pipeline.face_clustering import _FACE_EXACT_SEARCH_MAX
        assert _FACE_EXACT_SEARCH_MAX > 0

    def test_hnsw_m_positive(self):
        from src.pipeline.face_clustering import _FACE_HNSW_M
        assert _FACE_HNSW_M > 0


# ---------------------------------------------------------------------------
# pipeline.media_analysis_tier1: additional constants
# ---------------------------------------------------------------------------

class TestMediaAnalysisTier1Additional:
    def test_ocr_max_dim_positive(self):
        from src.pipeline.media_analysis_tier1 import _OCR_MAX_DIM
        assert _OCR_MAX_DIM > 0

    def test_max_ocr_text_len_positive(self):
        from src.pipeline.media_analysis_tier1 import _MAX_OCR_TEXT_LEN
        assert _MAX_OCR_TEXT_LEN > 0

    def test_ocr_priority_2_non_empty(self):
        from src.pipeline.media_analysis_tier1 import _OCR_PRIORITY_2
        assert len(_OCR_PRIORITY_2) > 0

    def test_media_face_match_max_large(self):
        from src.pipeline.media_analysis_tier1 import MEDIA_FACE_MATCH_MAX
        # Should be at least a few thousand for useful face match
        assert MEDIA_FACE_MATCH_MAX >= 1000

    def test_rebuild_face_match_signals_callable(self):
        from src.pipeline.media_analysis_tier1 import rebuild_face_match_signals
        assert callable(rebuild_face_match_signals)


# ---------------------------------------------------------------------------
# pipeline.media_analysis: additional EXIF + batch size constants
# ---------------------------------------------------------------------------

class TestMediaAnalysisConstants:
    def test_exif_make_tag(self):
        from src.pipeline.media_analysis import _EXIF_MAKE
        assert _EXIF_MAKE == 271

    def test_exif_model_tag(self):
        from src.pipeline.media_analysis import _EXIF_MODEL
        assert _EXIF_MODEL == 272

    def test_exif_body_serial_tag(self):
        from src.pipeline.media_analysis import _EXIF_BODY_SERIAL
        assert _EXIF_BODY_SERIAL == 42033

    def test_exif_lens_serial_tag(self):
        from src.pipeline.media_analysis import _EXIF_LENS_SERIAL
        assert _EXIF_LENS_SERIAL == 42037

    def test_gps_cluster_precision_positive(self):
        from src.pipeline.media_analysis import _GPS_CLUSTER_PRECISION
        assert _GPS_CLUSTER_PRECISION > 0

    def test_gps_time_window_positive(self):
        from src.pipeline.media_analysis import _GPS_TIME_WINDOW
        from datetime import timedelta
        assert isinstance(_GPS_TIME_WINDOW, timedelta)
        assert _GPS_TIME_WINDOW.total_seconds() > 0

    def test_exif_batch_size_positive(self):
        from src.pipeline.media_analysis import MEDIA_EXIF_BATCH_SIZE
        assert MEDIA_EXIF_BATCH_SIZE > 0

    def test_phash_batch_size_positive(self):
        from src.pipeline.media_analysis import MEDIA_PHASH_BATCH_SIZE
        assert MEDIA_PHASH_BATCH_SIZE > 0

    def test_drive_available_callable(self):
        from src.pipeline.media_analysis import _drive_available
        assert isinstance(_drive_available(), bool)

    def test_exif_content_types_list(self):
        from src.pipeline.media_analysis import _EXIF_CONTENT_TYPES
        assert len(_EXIF_CONTENT_TYPES) > 0
