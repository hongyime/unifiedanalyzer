"""
Pure-function tests — batch 34.

Covers previously untested pure functions:
- pipeline.location_evidence: _geometry_from_item, _first_point_coord,
  _first_coord, _first_present, evidence_key_from_location_ref,
  attach_location_evidence_key, SUPPRESSED_LOCATION_STATUSES
- pipeline.entity_resolver: name_is_distinctive boundaries, name_block_keys
  edge cases (already partially covered — adding load_commit_emails is async,
  so testing PlatformProfile/SignalMatch/EntityCandidate dataclasses)
- pipeline.face_clustering: _DERIVED_PROPAGATION_METHODS membership completeness
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# location_evidence: remaining pure helpers
# ---------------------------------------------------------------------------

class TestGeometryFromItem:
    def _g(self, item):
        from src.pipeline.location_evidence import _geometry_from_item
        return _geometry_from_item(item)

    def test_with_points_list(self):
        result = self._g({"points": [[1.3, 103.8], [1.4, 103.9]]})
        assert result["type"] == "LineString"
        assert len(result["points"]) == 2

    def test_empty_points_returns_empty(self):
        assert self._g({"points": []}) == {}

    def test_no_points_returns_empty(self):
        assert self._g({}) == {}

    def test_non_list_points_returns_empty(self):
        assert self._g({"points": "invalid"}) == {}


class TestFirstPointCoord:
    def _p(self, points, idx):
        from src.pipeline.location_evidence import _first_point_coord
        return _first_point_coord(points, idx)

    def test_first_lat(self):
        result = self._p([[1.3, 103.8], [2.0, 104.0]], 0)
        assert abs(result - 1.3) < 1e-9

    def test_first_lng(self):
        result = self._p([[1.3, 103.8]], 1)
        assert abs(result - 103.8) < 1e-9

    def test_empty_list_returns_none(self):
        assert self._p([], 0) is None

    def test_none_returns_none(self):
        assert self._p(None, 0) is None

    def test_non_list_returns_none(self):
        assert self._p("invalid", 0) is None


class TestFirstCoord:
    def _f(self, value, idx):
        from src.pipeline.location_evidence import _first_coord
        return _first_coord(value, idx)

    def test_list_lat(self):
        assert abs(self._f([1.3, 103.8], 0) - 1.3) < 1e-9

    def test_list_lng(self):
        assert abs(self._f([1.3, 103.8], 1) - 103.8) < 1e-9

    def test_tuple_works(self):
        assert abs(self._f((1.3, 103.8), 0) - 1.3) < 1e-9

    def test_none_returns_none(self):
        assert self._f(None, 0) is None

    def test_string_returns_none(self):
        assert self._f("1.3,103.8", 0) is None

    def test_index_out_of_range_returns_none(self):
        assert self._f([1.3], 1) is None


class TestFirstPresent:
    def _fp(self, *values):
        from src.pipeline.location_evidence import _first_present
        return _first_present(*values)

    def test_returns_first_non_none(self):
        assert self._fp(None, None, 42) == 42

    def test_returns_first_if_all_non_none(self):
        assert self._fp(1, 2, 3) == 1

    def test_all_none_returns_none(self):
        assert self._fp(None, None) is None

    def test_zero_is_valid_value(self):
        assert self._fp(None, 0) == 0

    def test_empty_string_is_valid(self):
        assert self._fp(None, "") == ""


class TestSuppressedLocationStatuses:
    def test_rejected_and_suppressed_in_set(self):
        from src.pipeline.location_evidence import SUPPRESSED_LOCATION_STATUSES
        assert "rejected" in SUPPRESSED_LOCATION_STATUSES
        assert "suppressed" in SUPPRESSED_LOCATION_STATUSES

    def test_active_not_in_set(self):
        from src.pipeline.location_evidence import SUPPRESSED_LOCATION_STATUSES
        assert "active" not in SUPPRESSED_LOCATION_STATUSES


class TestEvidenceKeyFromLocationRef:
    def test_with_explicit_key(self):
        from src.pipeline.location_evidence import evidence_key_from_location_ref
        explicit_key = "a" * 64  # 64-char hex-like string
        result = evidence_key_from_location_ref("eid", {"evidence_key": explicit_key})
        assert result == explicit_key.lower()

    def test_without_explicit_key_computes_hash(self):
        from src.pipeline.location_evidence import evidence_key_from_location_ref
        result = evidence_key_from_location_ref("eid", {"source": "strava", "evidence_type": "route"})
        assert result is not None
        assert len(result) == 64

    def test_no_source_or_type_returns_none(self):
        from src.pipeline.location_evidence import evidence_key_from_location_ref
        assert evidence_key_from_location_ref("eid", {}) is None


class TestAttachLocationEvidenceKey:
    def test_adds_evidence_key(self):
        from src.pipeline.location_evidence import attach_location_evidence_key
        item = {"source": "strava", "evidence_type": "route", "source_record_id": "r1"}
        result = attach_location_evidence_key("eid-1", item)
        assert "evidence_key" in result
        assert len(result["evidence_key"]) == 64

    def test_original_item_unchanged(self):
        from src.pipeline.location_evidence import attach_location_evidence_key
        item = {"source": "strava", "evidence_type": "route"}
        _ = attach_location_evidence_key("eid-1", item)
        assert "evidence_key" not in item


# ---------------------------------------------------------------------------
# entity_resolver: PlatformProfile, SignalMatch, EntityCandidate dataclasses
# ---------------------------------------------------------------------------

class TestPlatformProfile:
    def test_required_fields(self):
        from src.pipeline.entity_resolver import PlatformProfile
        p = PlatformProfile(source="instagram", platform_id="123")
        assert p.source == "instagram"
        assert p.platform_id == "123"

    def test_optional_fields_default_none(self):
        from src.pipeline.entity_resolver import PlatformProfile
        p = PlatformProfile(source="github", platform_id="456")
        assert p.username is None
        assert p.name is None
        assert p.email is None
        assert p.phone is None

    def test_full_profile(self):
        from src.pipeline.entity_resolver import PlatformProfile
        p = PlatformProfile(
            source="github", platform_id="789",
            username="octocat", name="Octocat",
            email="octocat@github.com", phone=None,
        )
        assert p.username == "octocat"
        assert p.email == "octocat@github.com"


class TestSignalMatch:
    def test_fields(self):
        from src.pipeline.entity_resolver import SignalMatch
        s = SignalMatch(
            signal_type="email_match",
            source_platform="instagram",
            target_platform="github",
            source_record_id="src-1",
            target_record_id="tgt-1",
            value="user@example.com",
            confidence=0.9,
        )
        assert s.signal_type == "email_match"
        assert s.confidence == 0.9


class TestEntityCandidate:
    def test_default_empty_lists(self):
        from src.pipeline.entity_resolver import EntityCandidate
        c = EntityCandidate()
        assert c.profiles == []
        assert c.signals == []
        assert c.merge_policy_key is None

    def test_with_profiles(self):
        from src.pipeline.entity_resolver import EntityCandidate, PlatformProfile
        p = PlatformProfile(source="instagram", platform_id="123")
        c = EntityCandidate(profiles=[p])
        assert len(c.profiles) == 1


# ---------------------------------------------------------------------------
# face_clustering: _DERIVED_PROPAGATION_METHODS completeness
# ---------------------------------------------------------------------------

class TestDerivedPropagationMethods:
    def test_all_known_methods_present(self):
        from src.pipeline.face_clustering import _DERIVED_PROPAGATION_METHODS
        for method in ("cluster_propagation", "drive_cross_ref",
                       "drive_cross_ref_knn", "face_cluster", "knn_propagation"):
            assert method in _DERIVED_PROPAGATION_METHODS, f"{method} missing"

    def test_face_bridge_audit_matches(self):
        from src.pipeline.face_clustering import _DERIVED_PROPAGATION_METHODS
        from src.pipeline.face_bridge_audit import _UNSAFE_CLUSTER_METHODS
        # Both should contain the same propagation method names
        for method in _UNSAFE_CLUSTER_METHODS:
            assert method in _DERIVED_PROPAGATION_METHODS
