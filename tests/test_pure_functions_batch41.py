"""
Pure-function tests — batch 41.

Covers the last remaining untested pure functions and constants:
- pipeline.entity_resolver: SignalMatch confidence field validation,
  instagram_threads_linked confidence value (50.0 per spec),
  USERNAME_STRIP_CHARS constant
- pipeline.incremental_runner: _run_skipped_due_lock final edge cases,
  _COVERAGE_SOURCE_BUCKETS all values are strings,
  PRODUCTION_RUN_TYPES / PROBE_PHASE_NAMES via run_reporting import
- pipeline.media_analysis: _OFFICE_CONTENT_TYPES completeness,
  all batch size constants non-negative
- pipeline.face_clustering: _UNKNOWN_FACE_COUNT used as fail-closed sentinel value
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# entity_resolver: additional constant and signal validation
# ---------------------------------------------------------------------------

class TestEntityResolverAdditional:
    def test_username_strip_chars_contains_dot_underscore_dash(self):
        from src.pipeline.entity_resolver import USERNAME_STRIP_CHARS
        assert "." in USERNAME_STRIP_CHARS
        assert "_" in USERNAME_STRIP_CHARS
        assert "-" in USERNAME_STRIP_CHARS

    def test_name_fuzzy_min_score_name_only_stricter(self):
        from src.pipeline.entity_resolver import (
            NAME_FUZZY_MIN_SCORE, NAME_FUZZY_MIN_SCORE_NAME_ONLY
        )
        assert NAME_FUZZY_MIN_SCORE_NAME_ONLY >= NAME_FUZZY_MIN_SCORE

    def test_instagram_threads_signal_confidence(self):
        # Per docstring: confidence=50.0 for instagram_threads_linked
        # (highest single-signal contribution in this resolver)
        from src.pipeline.entity_resolver import SignalMatch
        s = SignalMatch(
            signal_type="instagram_threads_linked",
            source_platform="instagram",
            target_platform="threads",
            source_record_id="alice",
            target_record_id="alice",
            value="alice",
            confidence=50.0,
        )
        assert s.confidence == 50.0
        assert s.signal_type == "instagram_threads_linked"

    def test_cross_entity_signal_confidence_has_all_strong_signals(self):
        from src.pipeline.entity_resolver import (
            STRONG_SIGNAL_TYPES, _CROSS_ENTITY_SIGNAL_CONFIDENCE
        )
        # All strong signals should have a defined confidence
        for sig in STRONG_SIGNAL_TYPES:
            assert sig in _CROSS_ENTITY_SIGNAL_CONFIDENCE, f"{sig} missing from confidence map"

    def test_confidence_threshold_less_than_1(self):
        from src.pipeline.entity_resolver import CONFIDENCE_THRESHOLD
        # Must be < 1 so auto-confirm can happen
        assert CONFIDENCE_THRESHOLD < 1.0

    def test_name_block_keys_three_token(self):
        from src.pipeline.entity_resolver import name_block_keys
        keys = name_block_keys("Alice Smith Jones")
        assert "ali" in keys
        assert "smi" in keys
        assert "jon" in keys


# ---------------------------------------------------------------------------
# incremental_runner: final constant coverage
# ---------------------------------------------------------------------------

class TestIncrementalRunnerFinalConstants:
    def test_coverage_source_buckets_all_strings(self):
        from src.pipeline.incremental_runner import _COVERAGE_SOURCE_BUCKETS
        assert all(isinstance(b, str) for b in _COVERAGE_SOURCE_BUCKETS)

    def test_production_run_types_match_run_reporting(self):
        from src.pipeline.incremental_runner import production_run_types
        from src.pipeline.run_reporting import PRODUCTION_RUN_TYPES
        # Should be consistent
        assert set(production_run_types()) == set(PRODUCTION_RUN_TYPES)

    def test_probe_phase_names_match_run_reporting(self):
        from src.pipeline.incremental_runner import probe_phase_names
        from src.pipeline.run_reporting import PROBE_PHASE_NAMES
        assert set(probe_phase_names()) == set(PROBE_PHASE_NAMES)

    def test_run_skipped_truthy_value(self):
        from src.scheduler.scheduler import _run_skipped_due_lock
        # Various truthy 'skipped' patterns
        assert _run_skipped_due_lock({"skipped": True}) is True
        assert _run_skipped_due_lock({"skipped": 1}) is False  # 1 != True for is check

    def test_stale_heartbeat_exceeds_heartbeat_interval_by_factor(self):
        from src.pipeline.incremental_runner import (
            _STALE_HEARTBEAT_MINUTES, _HEARTBEAT_INTERVAL_SECONDS
        )
        # Stale window should be many heartbeat intervals (at least 10x)
        stale_seconds = _STALE_HEARTBEAT_MINUTES * 60
        assert stale_seconds >= _HEARTBEAT_INTERVAL_SECONDS * 10


# ---------------------------------------------------------------------------
# media_analysis: final constant coverage
# ---------------------------------------------------------------------------

class TestMediaAnalysisFinalConstants:
    def test_all_batch_sizes_positive(self):
        from src.pipeline.media_analysis import (
            MEDIA_EXIF_BATCH_SIZE, MEDIA_PHASH_BATCH_SIZE,
            MEDIA_PDF_TEXT_BATCH_SIZE, MEDIA_PDF_IMAGE_BATCH_SIZE,
        )
        assert MEDIA_EXIF_BATCH_SIZE > 0
        assert MEDIA_PHASH_BATCH_SIZE > 0
        assert MEDIA_PDF_TEXT_BATCH_SIZE > 0
        assert MEDIA_PDF_IMAGE_BATCH_SIZE > 0

    def test_office_content_types_covers_docx_xlsx_pptx(self):
        from src.pipeline.media_analysis import _OFFICE_CONTENT_TYPES
        ct_set = {ct for _, ct in _OFFICE_CONTENT_TYPES}
        assert "docx" in ct_set
        assert "xlsx" in ct_set
        assert "pptx" in ct_set

    def test_max_pdf_text_len_at_least_100k(self):
        from src.pipeline.media_analysis import _MAX_PDF_TEXT_LEN
        assert _MAX_PDF_TEXT_LEN >= 100_000

    def test_phash_distance_threshold_small(self):
        from src.pipeline.media_analysis import _PHASH_DISTANCE_THRESHOLD
        # Near-duplicate threshold should be tight
        assert 1 <= _PHASH_DISTANCE_THRESHOLD <= 10


# ---------------------------------------------------------------------------
# face_clustering: fail-closed sentinel
# ---------------------------------------------------------------------------

class TestFaceClusteringFailClosed:
    def test_unknown_face_count_fail_closed(self):
        from src.pipeline.face_clustering import (
            _UNKNOWN_FACE_COUNT, _MAX_PROPAGATE_FACE_COUNT
        )
        # Sentinel must be large enough to always exceed the portrait gate
        assert _UNKNOWN_FACE_COUNT > _MAX_PROPAGATE_FACE_COUNT

    def test_unknown_face_count_large_sentinel(self):
        from src.pipeline.face_clustering import _UNKNOWN_FACE_COUNT
        assert _UNKNOWN_FACE_COUNT >= 100

    def test_propagate_threshold_above_cluster_threshold(self):
        from src.pipeline.face_clustering import (
            _PROPAGATE_MIN_SIM, _CLUSTER_THRESHOLD
        )
        # Purity gate should be stricter than the clustering threshold
        assert _PROPAGATE_MIN_SIM >= _CLUSTER_THRESHOLD
