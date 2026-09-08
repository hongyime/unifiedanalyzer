"""
Pure-function tests — batch 53.

Covers previously untested pure functions:
- util.audit_log: _clean_str, _walk_entity_snapshots, _evidence_snapshot_from_payload,
  _stable_refs_from_payload, _validate_decision_event (valid/invalid cases)
"""
from __future__ import annotations

import hashlib
import pytest


# ---------------------------------------------------------------------------
# util.audit_log: _clean_str
# ---------------------------------------------------------------------------

class TestCleanStr:
    def _c(self, v):
        from src.util.audit_log import _clean_str
        return _clean_str(v)

    def test_plain_string(self):
        assert self._c("hello") == "hello"

    def test_strips_whitespace(self):
        assert self._c("  hello  ") == "hello"

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_empty_string_returns_none(self):
        assert self._c("") is None

    def test_whitespace_only_returns_none(self):
        assert self._c("   ") is None

    def test_converts_int(self):
        assert self._c(42) == "42"


# ---------------------------------------------------------------------------
# util.audit_log: _walk_entity_snapshots
# ---------------------------------------------------------------------------

class TestWalkEntitySnapshots:
    def _w(self, value):
        from src.util.audit_log import _walk_entity_snapshots
        return list(_walk_entity_snapshots(value))

    def test_dict_with_platform_links_yielded(self):
        snapshot = {"entity_id": "e1", "platform_links": [{"source": "instagram"}]}
        result = self._w(snapshot)
        assert snapshot in result

    def test_nested_snapshot_found(self):
        outer = {
            "entity_snapshot": {"entity_id": "e1", "platform_links": [{"source": "instagram"}]}
        }
        result = self._w(outer)
        assert any(isinstance(r, dict) and "platform_links" in r for r in result)

    def test_list_of_snapshots(self):
        s1 = {"entity_id": "e1", "platform_links": []}
        s2 = {"entity_id": "e2", "platform_links": []}
        result = self._w([s1, s2])
        assert s1 in result
        assert s2 in result

    def test_no_platform_links_not_yielded(self):
        d = {"entity_id": "e1", "other": "data"}
        result = self._w(d)
        assert d not in result

    def test_empty_dict_not_yielded(self):
        assert self._w({}) == []

    def test_empty_list_not_yielded(self):
        assert self._w([]) == []


# ---------------------------------------------------------------------------
# util.audit_log: _evidence_snapshot_from_payload
# ---------------------------------------------------------------------------

class TestEvidenceSnapshotFromPayload:
    def _e(self, payload):
        from src.util.audit_log import _evidence_snapshot_from_payload
        return _evidence_snapshot_from_payload(payload)

    def test_empty_payload(self):
        assert self._e({}) == {}

    def test_known_keys_extracted(self):
        payload = {"confidence": 0.9, "reason": "test reason", "unknown_key": "ignored"}
        result = self._e(payload)
        assert "confidence" in result
        assert "reason" in result
        assert "unknown_key" not in result

    def test_notes_extracted(self):
        result = self._e({"notes": "some notes"})
        assert result["notes"] == "some notes"

    def test_location_ref_extracted(self):
        ref = {"source": "strava", "lat": 1.35}
        result = self._e({"location_ref": ref})
        assert result["location_ref"] == ref

    def test_unrelated_keys_excluded(self):
        result = self._e({"target_entity_id": "eid", "merged_count": 2})
        assert "target_entity_id" not in result
        assert "merged_count" not in result


# ---------------------------------------------------------------------------
# util.audit_log: _stable_refs_from_payload
# ---------------------------------------------------------------------------

class TestStableRefsFromPayload:
    def _s(self, payload):
        from src.util.audit_log import _stable_refs_from_payload
        return _stable_refs_from_payload(payload)

    def test_empty_payload(self):
        assert self._s({}) == []

    def test_platform_link_extracted(self):
        payload = {
            "entity_snapshot": [{
                "entity_id": "e1",
                "platform_links": [{"source": "instagram", "platform_id": "12345"}],
            }]
        }
        result = self._s(payload)
        assert len(result) >= 1
        assert result[0]["source"] == "instagram"
        assert result[0]["platform_id"] == "12345"

    def test_media_ref_extracted(self):
        payload = {
            "media_ref": {"source": "instagram", "sha256": "a" * 64, "content_id": "mid-1"}
        }
        result = self._s(payload)
        assert any(r["source"] == "instagram" for r in result)

    def test_deduplicates_refs(self):
        link = {"source": "instagram", "platform_id": "12345"}
        payload = {
            "entity_snapshot": [
                {"platform_links": [link]},
                {"platform_links": [link]},
            ]
        }
        result = self._s(payload)
        # Same ref shouldn't appear twice
        seen = [(r["source"], r["platform_id"]) for r in result]
        assert len(seen) == len(set(seen))

    def test_no_source_excluded(self):
        payload = {
            "entity_snapshot": [{"platform_links": [{"platform_id": "123"}]}]
        }
        result = self._s(payload)
        assert result == []


# ---------------------------------------------------------------------------
# util.audit_log: _validate_decision_event edge cases
# ---------------------------------------------------------------------------

class TestValidateDecisionEvent:
    def _valid_event(self):
        h = hashlib.sha256(b"test").hexdigest()
        return {
            "schema_version": 1,
            "audit_id": 1,
            "event_type": "merge_confirmed",
            "actor": "dashboard",
            "entity_ids": ["eid-1"],
            "payload": {},
            "created_at": "2026-01-15T12:00:00.000000",
            "prev_sha256": None,
            "sha256": h,
            "idempotency_key": h,
        }

    def test_valid_event_no_raise(self):
        from src.util.audit_log import _validate_decision_event
        _validate_decision_event(self._valid_event())  # should not raise

    def test_missing_field_raises(self):
        from src.util.audit_log import _validate_decision_event
        event = self._valid_event()
        del event["sha256"]
        with pytest.raises(ValueError, match="missing fields"):
            _validate_decision_event(event)

    def test_invalid_event_type_raises(self):
        from src.util.audit_log import _validate_decision_event
        event = self._valid_event()
        event["event_type"] = "not_a_real_event"
        with pytest.raises(ValueError, match="not supported"):
            _validate_decision_event(event)

    def test_negative_audit_id_raises(self):
        from src.util.audit_log import _validate_decision_event
        event = self._valid_event()
        event["audit_id"] = -1
        with pytest.raises(ValueError, match="positive"):
            _validate_decision_event(event)

    def test_invalid_sha256_raises(self):
        from src.util.audit_log import _validate_decision_event
        event = self._valid_event()
        event["sha256"] = "not-a-sha256"
        with pytest.raises(ValueError, match="sha256"):
            _validate_decision_event(event)
