"""
Pure-function tests — batch 93.

Covers:
- util.audit_log: _normalize_idempotency_key, _hash, _row_value,
  _decision_event_idempotency_key
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# util.audit_log: _normalize_idempotency_key
# ---------------------------------------------------------------------------

class TestNormalizeIdempotencyKey:
    def _n(self, v):
        from src.util.audit_log import _normalize_idempotency_key
        return _normalize_idempotency_key(v)

    def test_none_returns_none(self):
        assert self._n(None) is None

    def test_already_sha256_hex_returned_lowercase(self):
        key = "a" * 64
        assert self._n(key) == key

    def test_non_hex_string_hashed(self):
        result = self._n("my-custom-key")
        assert result is not None
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._n("key-abc") == self._n("key-abc")

    def test_strips_whitespace_before_hashing(self):
        assert self._n("  key-abc  ") == self._n("key-abc")

    def test_lowercased_before_hashing(self):
        assert self._n("KEY-ABC") == self._n("key-abc")


# ---------------------------------------------------------------------------
# util.audit_log: _hash
# ---------------------------------------------------------------------------

class TestAuditHash:
    def _h(self, prev=None, action="merge_entities", actor=None,
           entity_ids=None, payload=None, created_at_iso="2026-01-01T00:00:00.000000+00:00"):
        from src.util.audit_log import _hash
        return _hash(
            prev,
            action,
            actor,
            entity_ids,
            payload or {},
            created_at_iso,
        )

    def test_returns_64_hex(self):
        result = self._h()
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._h() == self._h()

    def test_different_action_different_hash(self):
        assert self._h(action="merge_entities") != self._h(action="dismiss_match")

    def test_different_prev_different_hash(self):
        a = self._h(prev=None)
        b = self._h(prev="a" * 64)
        assert a != b

    def test_entity_ids_order_independent(self):
        a = self._h(entity_ids=["eid-1", "eid-2"])
        b = self._h(entity_ids=["eid-2", "eid-1"])
        assert a == b

    def test_different_timestamps_different_hash(self):
        a = self._h(created_at_iso="2026-01-01T00:00:00.000000+00:00")
        b = self._h(created_at_iso="2026-06-01T00:00:00.000000+00:00")
        assert a != b


# ---------------------------------------------------------------------------
# util.audit_log: _row_value
# ---------------------------------------------------------------------------

class TestAuditRowValue:
    def _r(self, row, key, default=None):
        from src.util.audit_log import _row_value
        return _row_value(row, key, default)

    def test_dict_found(self):
        assert self._r({"k": 42}, "k") == 42

    def test_dict_missing_returns_default(self):
        assert self._r({}, "k", "fb") == "fb"

    def test_none_row_returns_default(self):
        assert self._r(None, "k", 0) == 0

    def test_list_row(self):
        assert self._r(["a", "b", "c"], 1) == "b"


# ---------------------------------------------------------------------------
# util.audit_log: _decision_event_idempotency_key
# ---------------------------------------------------------------------------

class TestDecisionEventIdempotencyKey:
    def _k(self, action="merge_entities", actor=None, entity_ids=None,
           payload=None, created_at=None):
        from src.util.audit_log import _decision_event_idempotency_key
        created_at = created_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
        return _decision_event_idempotency_key(
            action=action,
            actor=actor,
            entity_ids=entity_ids or [],
            payload=payload or {},
            created_at=created_at,
        )

    def test_returns_64_hex(self):
        result = self._k()
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._k() == self._k()

    def test_different_action_different_key(self):
        assert self._k(action="merge_entities") != self._k(action="dismiss_match")

    def test_entity_ids_order_independent(self):
        a = self._k(entity_ids=["eid-1", "eid-2"])
        b = self._k(entity_ids=["eid-2", "eid-1"])
        assert a == b

    def test_different_timestamp_different_key(self):
        dt1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        assert self._k(created_at=dt1) != self._k(created_at=dt2)
