"""
Pure-function tests — batch 54.

Covers previously untested pure functions:
- util.audit_log: _hash, _row_value, _jsonl_contains_event (file-based)
- api.routes.graph: additional confidence_bucket cases + _relationship_why completeness
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest


# ---------------------------------------------------------------------------
# util.audit_log: _hash
# ---------------------------------------------------------------------------

class TestAuditHash:
    def _h(self, prev_sha256=None, action="merge_confirmed", actor="dashboard",
           entity_ids=None, payload=None, created_at_iso="2026-01-15T12:00:00.000000"):
        from src.util.audit_log import _hash
        return _hash(prev_sha256, action, actor, entity_ids or [],
                     payload or {}, created_at_iso)

    def test_returns_64_hex_chars(self):
        result = self._h()
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._h() == self._h()

    def test_different_actions_different_hashes(self):
        assert self._h(action="merge_confirmed") != self._h(action="split_person")

    def test_prev_sha256_affects_hash(self):
        h = "a" * 64
        assert self._h(prev_sha256=None) != self._h(prev_sha256=h)

    def test_entity_ids_sorted_stable(self):
        # Order of entity_ids shouldn't matter
        assert self._h(entity_ids=["b", "a"]) == self._h(entity_ids=["a", "b"])

    def test_different_timestamps_different_hashes(self):
        h1 = self._h(created_at_iso="2026-01-15T12:00:00.000000")
        h2 = self._h(created_at_iso="2026-01-15T12:00:01.000000")
        assert h1 != h2

    def test_actor_affects_hash(self):
        assert self._h(actor="alice") != self._h(actor="bob")

    def test_none_actor_stable(self):
        # None actor should be treated consistently
        h1 = self._h(actor=None)
        h2 = self._h(actor=None)
        assert h1 == h2


# ---------------------------------------------------------------------------
# util.audit_log: _row_value
# ---------------------------------------------------------------------------

class TestRowValue:
    def _r(self, row, key, default=None):
        from src.util.audit_log import _row_value
        return _row_value(row, key, default)

    def test_dict_present(self):
        assert self._r({"k": "v"}, "k") == "v"

    def test_dict_missing_default(self):
        assert self._r({"k": 1}, "x", "fallback") == "fallback"

    def test_none_row_default(self):
        assert self._r(None, "k", "d") == "d"

    def test_subscriptable_object(self):
        # Works like a dict
        class FakeRow:
            def __getitem__(self, key):
                return {"audit_id": 42}[key]
        assert self._r(FakeRow(), "audit_id") == 42


# ---------------------------------------------------------------------------
# util.audit_log: _jsonl_contains_event
# ---------------------------------------------------------------------------

class TestJsonlContainsEvent:
    def test_missing_file_returns_false(self, tmp_path):
        from src.util.audit_log import _jsonl_contains_event
        path = tmp_path / "nonexistent.jsonl"
        assert _jsonl_contains_event(path, audit_id=1, idempotency_key=None) is False

    def test_finds_by_audit_id(self, tmp_path):
        from src.util.audit_log import _jsonl_contains_event
        path = tmp_path / "log.jsonl"
        path.write_text(json.dumps({"audit_id": 42, "event_type": "merge_confirmed"}) + "\n")
        assert _jsonl_contains_event(path, audit_id=42, idempotency_key=None) is True

    def test_not_found_by_audit_id(self, tmp_path):
        from src.util.audit_log import _jsonl_contains_event
        path = tmp_path / "log.jsonl"
        path.write_text(json.dumps({"audit_id": 1}) + "\n")
        assert _jsonl_contains_event(path, audit_id=99, idempotency_key=None) is False

    def test_finds_by_idempotency_key(self, tmp_path):
        from src.util.audit_log import _jsonl_contains_event
        key = hashlib.sha256(b"test").hexdigest()
        path = tmp_path / "log.jsonl"
        path.write_text(json.dumps({"audit_id": 1, "idempotency_key": key}) + "\n")
        assert _jsonl_contains_event(path, audit_id=99, idempotency_key=key) is True

    def test_empty_file_returns_false(self, tmp_path):
        from src.util.audit_log import _jsonl_contains_event
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        assert _jsonl_contains_event(path, audit_id=1, idempotency_key=None) is False

    def test_invalid_json_lines_skipped(self, tmp_path):
        from src.util.audit_log import _jsonl_contains_event
        path = tmp_path / "log.jsonl"
        path.write_text("bad-json\n" + json.dumps({"audit_id": 5}) + "\n")
        assert _jsonl_contains_event(path, audit_id=5, idempotency_key=None) is True


# ---------------------------------------------------------------------------
# api.routes.graph: _relationship_why additional coverage
# ---------------------------------------------------------------------------

class TestRelationshipWhyAdditional:
    def _w(self, rtype, sources):
        from src.api.routes.graph import _relationship_why
        return _relationship_why(rtype, sources)

    def test_same_person_probability_with_score(self):
        sources = {
            "score": 0.85,
            "contributing_signals": [
                {"type": "email_match"},
                {"type": "phone_match"},
            ]
        }
        result = self._w("same_person_probability", sources)
        assert result is not None
        assert "0.85" in result

    def test_same_person_no_score_returns_none(self):
        result = self._w("same_person_probability", {})
        assert result is None

    def test_group_co_member_with_more_groups(self):
        sources = {"groups": ["A", "B", "C", "D"]}
        result = self._w("telegram_group_co_member", sources)
        assert result is not None
        assert "(+" in result or "more" in result.lower()

    def test_temporal_copost_with_events(self):
        sources = {"coincident_events": 5, "copost_days": 3}
        result = self._w("temporal_copost", sources)
        assert result is not None
        assert "5" in result

    def test_social_graph_overlap_with_details(self):
        sources = {"shared": 12, "jaccard": 0.38}
        result = self._w("social_graph_overlap", sources)
        assert result is not None
        assert "12" in result

    def test_explicit_why_overrides(self):
        sources = {"why": "They share a device fingerprint"}
        result = self._w("media_device_match", sources)
        assert result == "They share a device fingerprint"
