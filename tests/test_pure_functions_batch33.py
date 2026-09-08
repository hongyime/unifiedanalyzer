"""
Pure-function tests — batch 33.

Covers previously untested pure functions and constants:
- pipeline.entity_enrichment: _is_enabled, _max_chars, _batch_size, _model_name,
  _LABEL_BUCKETS constants
- pipeline.ig_geo_resolver: IG_LINK_METHOD, IG_LINK_CONFIDENCE, SCAN_TIMEOUT_SECONDS
- pipeline.strava_athlete_resolver: STRAVA_LINK_METHOD, STRAVA_LINK_CONFIDENCE
- pipeline.backfill_derived: _MARKERS constant
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.entity_enrichment: pure config helpers + constants
# ---------------------------------------------------------------------------

class TestEntityEnrichmentConfig:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("ENTITY_ENRICHMENT_ENABLED", raising=False)
        from src.pipeline.entity_enrichment import _is_enabled
        assert _is_enabled() is False

    def test_enabled_when_1(self, monkeypatch):
        monkeypatch.setenv("ENTITY_ENRICHMENT_ENABLED", "1")
        from src.pipeline.entity_enrichment import _is_enabled
        assert _is_enabled() is True

    def test_enabled_when_true(self, monkeypatch):
        monkeypatch.setenv("ENTITY_ENRICHMENT_ENABLED", "true")
        from src.pipeline.entity_enrichment import _is_enabled
        assert _is_enabled() is True

    def test_max_chars_default(self, monkeypatch):
        monkeypatch.delenv("NER_MAX_CHARS_PER_ENTITY", raising=False)
        from src.pipeline.entity_enrichment import _max_chars
        assert _max_chars() == 20000

    def test_max_chars_custom(self, monkeypatch):
        monkeypatch.setenv("NER_MAX_CHARS_PER_ENTITY", "5000")
        from src.pipeline.entity_enrichment import _max_chars
        assert _max_chars() == 5000

    def test_batch_size_default(self, monkeypatch):
        monkeypatch.delenv("NER_ENTITY_BATCH_PER_RUN", raising=False)
        from src.pipeline.entity_enrichment import _batch_size
        assert _batch_size() == 100

    def test_model_name_default(self, monkeypatch):
        monkeypatch.delenv("NER_MODEL", raising=False)
        from src.pipeline.entity_enrichment import _model_name
        assert _model_name() == "en_core_web_trf"

    def test_model_name_custom(self, monkeypatch):
        monkeypatch.setenv("NER_MODEL", "en_core_web_sm")
        from src.pipeline.entity_enrichment import _model_name
        assert _model_name() == "en_core_web_sm"

    def test_label_buckets_non_empty(self):
        from src.pipeline.entity_enrichment import _LABEL_BUCKETS
        assert len(_LABEL_BUCKETS) > 0

    def test_label_buckets_org_maps_to_employers_schools(self):
        from src.pipeline.entity_enrichment import _LABEL_BUCKETS
        assert _LABEL_BUCKETS.get("ORG") == "employers_schools"

    def test_label_buckets_gpe_maps_to_locations(self):
        from src.pipeline.entity_enrichment import _LABEL_BUCKETS
        assert _LABEL_BUCKETS.get("GPE") == "locations"

    def test_label_buckets_person_present(self):
        from src.pipeline.entity_enrichment import _LABEL_BUCKETS
        assert "PERSON" in _LABEL_BUCKETS


# ---------------------------------------------------------------------------
# pipeline.ig_geo_resolver: module-level constants
# ---------------------------------------------------------------------------

class TestIgGeoResolverConstants:
    def test_link_method_string(self):
        from src.pipeline.ig_geo_resolver import IG_LINK_METHOD
        assert isinstance(IG_LINK_METHOD, str)
        assert len(IG_LINK_METHOD) > 0

    def test_link_confidence_in_range(self):
        from src.pipeline.ig_geo_resolver import IG_LINK_CONFIDENCE
        assert 0.0 < IG_LINK_CONFIDENCE <= 1.0

    def test_scan_timeout_positive(self):
        from src.pipeline.ig_geo_resolver import SCAN_TIMEOUT_SECONDS
        assert SCAN_TIMEOUT_SECONDS > 0

    def test_substantive_sql_non_empty(self):
        from src.pipeline.ig_geo_resolver import _SUBSTANTIVE_IG_PROFILES_SQL
        assert isinstance(_SUBSTANTIVE_IG_PROFILES_SQL, str)
        assert len(_SUBSTANTIVE_IG_PROFILES_SQL) > 50


# ---------------------------------------------------------------------------
# pipeline.strava_athlete_resolver: module-level constants
# ---------------------------------------------------------------------------

class TestStravaAthleteResolverConstants:
    def test_link_method_string(self):
        from src.pipeline.strava_athlete_resolver import STRAVA_LINK_METHOD
        assert isinstance(STRAVA_LINK_METHOD, str)
        assert len(STRAVA_LINK_METHOD) > 0

    def test_link_confidence_in_range(self):
        from src.pipeline.strava_athlete_resolver import STRAVA_LINK_CONFIDENCE
        assert 0.0 < STRAVA_LINK_CONFIDENCE <= 1.0

    def test_scan_timeout_positive(self):
        from src.pipeline.strava_athlete_resolver import SCAN_TIMEOUT_SECONDS
        assert SCAN_TIMEOUT_SECONDS > 0

    def test_substantive_sql_non_empty(self):
        from src.pipeline.strava_athlete_resolver import _SUBSTANTIVE_STRAVA_ATHLETES_SQL
        assert isinstance(_SUBSTANTIVE_STRAVA_ATHLETES_SQL, str)
        assert "strava_athletes" in _SUBSTANTIVE_STRAVA_ATHLETES_SQL


# ---------------------------------------------------------------------------
# pipeline.backfill_derived: _MARKERS constant
# ---------------------------------------------------------------------------

class TestBackfillDerivedConstants:
    def test_markers_non_empty(self):
        from src.pipeline.backfill_derived import _MARKERS
        assert len(_MARKERS) >= 2

    def test_markers_have_three_elements(self):
        from src.pipeline.backfill_derived import _MARKERS
        for marker in _MARKERS:
            assert len(marker) == 3  # (analysis_type, count_key, checker_fn)

    def test_video_frames_marker_present(self):
        from src.pipeline.backfill_derived import _MARKERS
        types = [m[0] for m in _MARKERS]
        assert "video_frames" in types

    def test_pdf_embedded_image_marker_present(self):
        from src.pipeline.backfill_derived import _MARKERS
        types = [m[0] for m in _MARKERS]
        assert "pdf_embedded_image" in types

    def test_checker_functions_callable(self):
        from src.pipeline.backfill_derived import _MARKERS
        for _, _, checker in _MARKERS:
            assert callable(checker)
