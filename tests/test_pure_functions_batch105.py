"""
Pure-function tests — batch 105.

Covers:
- pipeline.location_evidence: evidence_key_from_location_ref,
  attach_location_evidence_key
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.location_evidence: evidence_key_from_location_ref
# ---------------------------------------------------------------------------

class TestEvidenceKeyFromLocationRef:
    def _k(self, entity_id, location_ref):
        from src.pipeline.location_evidence import evidence_key_from_location_ref
        return evidence_key_from_location_ref(entity_id, location_ref)

    def test_explicit_64_hex_key_returned_lowercase(self):
        key = "a" * 64
        result = self._k("eid-1", {"evidence_key": key})
        assert result == key.lower()

    def test_explicit_short_key_ignored(self):
        # Length != 64 → not returned directly, falls through to compute
        ref = {"evidence_key": "short", "source": "strava", "evidence_type": "gps"}
        result = self._k("eid-1", ref)
        assert result is not None
        assert len(result) == 64

    def test_no_source_and_no_evidence_type_returns_none(self):
        assert self._k("eid-1", {}) is None

    def test_source_without_evidence_type_computes_key(self):
        result = self._k("eid-1", {"source": "strava"})
        assert result is not None
        assert len(result) == 64

    def test_evidence_type_without_source_computes_key(self):
        result = self._k("eid-1", {"evidence_type": "gps"})
        assert result is not None
        assert len(result) == 64

    def test_deterministic(self):
        ref = {"source": "strava", "evidence_type": "gps", "lat": 1.35, "lng": 103.82}
        a = self._k("eid-1", ref)
        b = self._k("eid-1", ref)
        assert a == b

    def test_different_entity_different_key(self):
        ref = {"source": "strava", "evidence_type": "gps"}
        assert self._k("eid-1", ref) != self._k("eid-2", ref)

    def test_lat_lng_from_start_coords(self):
        # lat/lng can come from start[0]/start[1]
        ref = {"source": "strava", "evidence_type": "route", "start": [1.35, 103.82]}
        result = self._k("eid-1", ref)
        assert result is not None
        assert len(result) == 64


# ---------------------------------------------------------------------------
# pipeline.location_evidence: attach_location_evidence_key
# ---------------------------------------------------------------------------

class TestAttachLocationEvidenceKey:
    def _a(self, entity_id, item):
        from src.pipeline.location_evidence import attach_location_evidence_key
        return attach_location_evidence_key(entity_id, item)

    def test_returns_dict_with_evidence_key(self):
        item = {"source": "strava", "evidence_type": "gps", "lat": 1.35, "lng": 103.82}
        result = self._a("eid-1", item)
        assert "evidence_key" in result
        assert len(result["evidence_key"]) == 64

    def test_original_fields_preserved(self):
        item = {"source": "strava", "evidence_type": "gps", "custom": "value"}
        result = self._a("eid-1", item)
        assert result["custom"] == "value"
        assert result["source"] == "strava"

    def test_original_item_not_mutated(self):
        item = {"source": "strava", "evidence_type": "gps"}
        self._a("eid-1", item)
        assert "evidence_key" not in item

    def test_deterministic(self):
        item = {"source": "strava", "evidence_type": "gps", "lat": 1.35, "lng": 103.82}
        a = self._a("eid-1", item)
        b = self._a("eid-1", item)
        assert a["evidence_key"] == b["evidence_key"]

    def test_name_used_as_label_fallback(self):
        item_name = {"source": "strava", "evidence_type": "gps", "name": "Home"}
        item_label = {"source": "strava", "evidence_type": "gps", "label": "Home"}
        result_name = self._a("eid-1", item_name)
        result_label = self._a("eid-1", item_label)
        assert result_name["evidence_key"] == result_label["evidence_key"]
