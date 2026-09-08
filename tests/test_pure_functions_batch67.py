"""
Pure-function tests — batch 67.

Covers previously untested pure functions and final edge cases:
- api.routes.data_quality: _cache_path, _cache_ttl_seconds, _parse_ts, _cache_age_seconds
  additional edge cases
- api.routes.collector_health: _targets_by_source final edge cases
- api.routes.graph: remaining confidence_bucket and _relationship_why combinations
- api.routes.intersections: _haversine_m additional cases
- api.routes.timeline: _CONFIDENCE_METADATA_PATHS path lengths
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import math


# ---------------------------------------------------------------------------
# api.routes.data_quality: additional edge cases
# ---------------------------------------------------------------------------

class TestDataQualityCachePath:
    def test_returns_path_object(self, monkeypatch):
        monkeypatch.delenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", raising=False)
        from src.api.routes.data_quality import _cache_path
        from pathlib import Path
        result = _cache_path()
        assert isinstance(result, Path)

    def test_custom_path(self, monkeypatch, tmp_path):
        cache = str(tmp_path / "cache.json")
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", cache)
        from src.api.routes.data_quality import _cache_path
        assert str(_cache_path()) == cache


class TestDataQualityParseTs:
    def _p(self, v):
        from src.api.routes.data_quality import _parse_ts
        return _parse_ts(v)

    def test_valid_iso(self):
        result = self._p("2026-01-15T12:00:00Z")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_non_string_returns_none(self):
        assert self._p(42) is None

    def test_invalid_string_returns_none(self):
        assert self._p("not-a-date") is None

    def test_aware_datetime_converted(self):
        result = self._p("2026-06-01T00:00:00+08:00")
        assert result is not None
        assert result.tzinfo == timezone.utc


class TestDataQualityCacheAgeSeconds:
    def _a(self, payload):
        from src.api.routes.data_quality import _cache_age_seconds
        return _cache_age_seconds(payload)

    def test_fresh_payload(self):
        now_str = datetime.now(timezone.utc).isoformat()
        result = self._a({"generated_at": now_str})
        assert result is not None
        assert result >= 0

    def test_old_payload(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        result = self._a({"generated_at": old})
        assert result is not None
        assert result >= 7000  # >1 hour

    def test_missing_generated_at_returns_none(self):
        assert self._a({}) is None

    def test_invalid_generated_at_returns_none(self):
        assert self._a({"generated_at": "bad"}) is None


# ---------------------------------------------------------------------------
# api.routes.collector_health: _targets_by_source final edge cases
# ---------------------------------------------------------------------------

class TestTargetsBySourceFinal:
    def test_three_sources(self):
        from src.api.routes.collector_health import _targets_by_source
        targets = [
            {"source": "a", "status": "active", "count": 1, "last_collection": None},
            {"source": "b", "status": "active", "count": 2, "last_collection": None},
            {"source": "c", "status": "active", "count": 3, "last_collection": None},
        ]
        result = _targets_by_source(targets)
        assert set(result.keys()) == {"a", "b", "c"}

    def test_count_stored(self):
        from src.api.routes.collector_health import _targets_by_source
        targets = [{"source": "instagram", "status": "active", "count": 42, "last_collection": None}]
        result = _targets_by_source(targets)
        assert result["instagram"][0]["count"] == 42


# ---------------------------------------------------------------------------
# api.routes.graph: remaining coverage
# ---------------------------------------------------------------------------

class TestRelationshipWhyFinal:
    def _w(self, rtype, sources):
        from src.api.routes.graph import _relationship_why
        return _relationship_why(rtype, sources)

    def test_temporal_copost_days_only(self):
        # missing events key → returns the fallback posting-time overlap message
        sources = {"copost_days": 3}
        result = self._w("temporal_copost", sources)
        assert result is not None
        assert "overlap" in result.lower() or "posting" in result.lower()

    def test_group_co_member_few_groups(self):
        sources = {"groups": ["Family"]}
        result = self._w("whatsapp_group_co_member", sources)
        assert result is not None
        assert "Family" in result

    def test_empty_dict_sources(self):
        result = self._w("interaction", {})
        assert result is None

    def test_social_graph_overlap_missing_jaccard(self):
        sources = {"shared": 5}
        result = self._w("social_graph_overlap", sources)
        # shared without jaccard → condition fails
        assert result is None


class TestConfidenceBucketFinal:
    def _b(self, rtype, weight, cross=False):
        from src.api.routes.graph import confidence_bucket
        return confidence_bucket(rtype, weight, cross)

    def test_same_person_probability_exactly_80(self):
        assert self._b("same_person_probability", 80) == "hard"

    def test_same_person_probability_79(self):
        assert self._b("same_person_probability", 79) == "strong"

    def test_social_graph_overlap_high_weight_weak(self):
        assert self._b("social_graph_overlap", 10) == "weak"

    def test_face_coappearance_high_weight_weak(self):
        assert self._b("face_coappearance", 5) == "weak"

    def test_temporal_prefix(self):
        for rtype in ("temporal_hour_similarity", "temporal_copost"):
            assert self._b(rtype, 100) == "context-only"


# ---------------------------------------------------------------------------
# api.routes.intersections: _haversine_m additional precision cases
# ---------------------------------------------------------------------------

class TestHaversineMAdditional:
    def _h(self, a_lat, a_lng, b_lat, b_lng):
        from src.api.routes.intersections import _haversine_m
        return _haversine_m(a_lat, a_lng, b_lat, b_lng)

    def test_antipodal_approximately_20000km(self):
        # Opposite sides of Earth ~ 20,000km
        dist = self._h(0.0, 0.0, 0.0, 180.0)
        assert 19_900_000 < dist < 20_100_000

    def test_returns_float(self):
        assert isinstance(self._h(1.0, 103.0, 1.1, 103.1), float)

    def test_symmetric(self):
        d1 = self._h(1.35, 103.82, 3.14, 101.69)
        d2 = self._h(3.14, 101.69, 1.35, 103.82)
        assert abs(d1 - d2) < 0.001


# ---------------------------------------------------------------------------
# api.routes.timeline: _CONFIDENCE_METADATA_PATHS path content
# ---------------------------------------------------------------------------

class TestConfidenceMetadataPathsContent:
    def test_has_nested_paths(self):
        from src.api.routes.timeline import _CONFIDENCE_METADATA_PATHS
        nested = [p for p in _CONFIDENCE_METADATA_PATHS if len(p) > 1]
        assert len(nested) >= 1

    def test_confidence_key_appears_multiple_times(self):
        from src.api.routes.timeline import _CONFIDENCE_METADATA_PATHS
        count = sum(1 for p in _CONFIDENCE_METADATA_PATHS if "confidence" in p)
        assert count >= 2  # multiple paths contain "confidence"

    def test_all_tuples_non_empty(self):
        from src.api.routes.timeline import _CONFIDENCE_METADATA_PATHS
        for p in _CONFIDENCE_METADATA_PATHS:
            assert len(p) >= 1
