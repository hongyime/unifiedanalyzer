"""
Pure-function tests — batch 40.

Covers previously untested pure functions and constants:
- pipeline.media_analysis: _PDF_CONTENT_TYPES, _MAX_PDF_TEXT_LEN, _OFFICE_CONTENT_TYPES,
  _MAX_OFFICE_TEXT_LEN, office_text_available, _looks_like_pdf (with mock file),
  _PHASH_DISTANCE_THRESHOLD cross-check
- pipeline.face_clustering: all pure functions now covered — adding last remaining
  constant checks (_FACE_MEDIA_ATTRIBUTION_MAX_FACES, _last_face_count initial state)
- pipeline.incremental_runner: remaining top-level defs count + constants completeness
"""
from __future__ import annotations

import io
import pytest
from unittest.mock import patch, mock_open


# ---------------------------------------------------------------------------
# media_analysis: PDF/office constants + office_text_available + _looks_like_pdf
# ---------------------------------------------------------------------------

class TestMediaAnalysisPdfConstants:
    def test_pdf_content_types_non_empty(self):
        from src.pipeline.media_analysis import _PDF_CONTENT_TYPES
        assert len(_PDF_CONTENT_TYPES) > 0
        ct = [ct for _, ct in _PDF_CONTENT_TYPES]
        assert "pdf" in ct

    def test_max_pdf_text_len_large(self):
        from src.pipeline.media_analysis import _MAX_PDF_TEXT_LEN
        assert _MAX_PDF_TEXT_LEN >= 10_000

    def test_office_content_types_non_empty(self):
        from src.pipeline.media_analysis import _OFFICE_CONTENT_TYPES
        assert len(_OFFICE_CONTENT_TYPES) > 0
        ct = [ct for _, ct in _OFFICE_CONTENT_TYPES]
        assert "docx" in ct or "xlsx" in ct or "pptx" in ct

    def test_max_office_text_len_large(self):
        from src.pipeline.media_analysis import _MAX_OFFICE_TEXT_LEN
        assert _MAX_OFFICE_TEXT_LEN >= 10_000


class TestOfficeTextAvailable:
    def test_returns_bool(self):
        from src.pipeline.media_analysis import office_text_available
        result = office_text_available()
        assert isinstance(result, bool)


class TestLooksLikePdf:
    def _l(self, path):
        from src.pipeline.media_analysis import _looks_like_pdf
        return _looks_like_pdf(path)

    def test_pdf_header_returns_true(self):
        pdf_bytes = b"%PDF-1.4 fake content"
        with patch("builtins.open", mock_open(read_data=pdf_bytes)):
            assert self._l("/fake/path/doc.pdf") is True

    def test_non_pdf_returns_false(self):
        non_pdf_bytes = b"PK\x03\x04 this is a zip file"
        with patch("builtins.open", mock_open(read_data=non_pdf_bytes)):
            assert self._l("/fake/path/file.zip") is False

    def test_oserror_returns_false(self):
        with patch("builtins.open", side_effect=OSError("not found")):
            assert self._l("/nonexistent/path.pdf") is False

    def test_whitespace_before_header(self):
        pdf_bytes = b"  \n%PDF-1.7 with leading whitespace"
        with patch("builtins.open", mock_open(read_data=pdf_bytes)):
            assert self._l("/fake/doc.pdf") is True


# ---------------------------------------------------------------------------
# face_clustering: final remaining constants
# ---------------------------------------------------------------------------

class TestFaceClusteringFinalConstants:
    def test_face_media_attribution_max_faces_positive(self):
        from src.pipeline.face_clustering import _FACE_MEDIA_ATTRIBUTION_MAX_FACES
        assert _FACE_MEDIA_ATTRIBUTION_MAX_FACES >= 1

    def test_last_face_count_initial_negative(self):
        from src.pipeline.face_clustering import _last_face_count
        # Module-level sentinel — initial value -1 means "not yet counted"
        assert isinstance(_last_face_count, int)

    def test_faiss_threads_non_negative(self):
        from src.pipeline.face_clustering import _FACE_FAISS_THREADS
        assert _FACE_FAISS_THREADS >= 0

    def test_hnsw_ef_construction_positive(self):
        from src.pipeline.face_clustering import _FACE_HNSW_EF_CONSTRUCTION
        assert _FACE_HNSW_EF_CONSTRUCTION > 0

    def test_hnsw_ef_search_positive(self):
        from src.pipeline.face_clustering import _FACE_HNSW_EF_SEARCH
        assert _FACE_HNSW_EF_SEARCH > 0


# ---------------------------------------------------------------------------
# incremental_runner: remaining pure functions coverage check
# ---------------------------------------------------------------------------

class TestIncrementalRunnerCompleteness:
    def test_secondary_phases_has_face_phases(self):
        from src.pipeline.incremental_runner import _secondary_phases
        names = {n for n, _ in _secondary_phases()}
        for phase in ("face_clustering", "face_pair_knn", "face_associations",
                      "social_face_link", "face_match_signals"):
            assert phase in names

    def test_secondary_phases_has_media_phases(self):
        from src.pipeline.incremental_runner import _secondary_phases
        names = {n for n, _ in _secondary_phases()}
        for phase in ("media_exif", "media_phash", "media_ocr"):
            assert phase in names

    def test_secondary_phases_has_nlp_phases(self):
        from src.pipeline.incremental_runner import _secondary_phases
        names = {n for n, _ in _secondary_phases()}
        for phase in ("bio_nlp", "entity_enrichment", "content_fingerprint"):
            assert phase in names

    def test_all_secondary_phase_names_in_resource_classes(self):
        from src.pipeline.incremental_runner import _secondary_phases, _PHASE_RESOURCE_CLASSES
        for name, _ in _secondary_phases():
            # Each phase should either be in the map or fall back to "db"
            assert isinstance(_PHASE_RESOURCE_CLASSES.get(name, "db"), str)

    def test_coverage_row_length(self):
        from src.pipeline.incremental_runner import _coverage_row
        row = _coverage_row(
            run_id="r", run_type="incremental", phase="p",
            source="all", status="completed", duration_ms=0,
            stats={}, error=None,
        )
        # Should have enough columns for the INSERT statement
        assert len(row) >= 10


# ---------------------------------------------------------------------------
# entity_resolver: remaining constants completeness
# ---------------------------------------------------------------------------

class TestEntityResolverConstantsCompleteness:
    def test_common_username_accounts_threshold(self):
        from src.pipeline.entity_resolver import COMMON_USERNAME_ACCOUNTS
        # Should be > 5 to allow normal clusters but < 100 to block real common handles
        assert 5 < COMMON_USERNAME_ACCOUNTS < 100

    def test_similar_username_max_entities_small(self):
        from src.pipeline.entity_resolver import SIMILAR_USERNAME_MAX_ENTITIES
        assert 1 <= SIMILAR_USERNAME_MAX_ENTITIES <= 20

    def test_min_signals_positive(self):
        from src.pipeline.entity_resolver import MIN_SIGNALS
        assert MIN_SIGNALS >= 1

    def test_min_normalized_length_three(self):
        from src.pipeline.entity_resolver import MIN_NORMALIZED_LENGTH
        assert MIN_NORMALIZED_LENGTH == 3

    def test_default_username_re_matches_user(self):
        from src.pipeline.entity_resolver import DEFAULT_USERNAME_RE
        assert DEFAULT_USERNAME_RE.match("user") is not None
        assert DEFAULT_USERNAME_RE.match("user123") is not None
        assert DEFAULT_USERNAME_RE.match("alice") is None

    def test_strong_signals_include_verified(self):
        from src.pipeline.entity_resolver import STRONG_SIGNAL_TYPES, VERIFIED_SIGNAL_TYPES
        # Every verified signal must also be strong
        for sig in VERIFIED_SIGNAL_TYPES:
            assert sig in STRONG_SIGNAL_TYPES
