"""
Pure-function tests — batch 52.

Covers previously untested pure functions:
- util.audit_log: _canonical_json, _is_sha256_hex, DECISION_EVENT_TYPES constant,
  _decision_log_path, _normalize_idempotency_key, DECISION_EVENT_SCHEMA_VERSION
- api.routes.alerts: _decode_jsonb
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# util.audit_log: _canonical_json, _is_sha256_hex, constants
# ---------------------------------------------------------------------------

class TestCanonicalJson:
    def _c(self, obj):
        from src.util.audit_log import _canonical_json
        return _canonical_json(obj)

    def test_deterministic(self):
        assert self._c({"b": 2, "a": 1}) == self._c({"a": 1, "b": 2})

    def test_keys_sorted(self):
        result = self._c({"z": 1, "a": 2})
        assert result.index('"a"') < result.index('"z"')

    def test_no_whitespace(self):
        result = self._c({"k": "v"})
        assert " " not in result

    def test_non_serializable_uses_str(self):
        from datetime import date
        result = self._c({"d": date(2026, 1, 1)})
        assert "2026" in result

    def test_empty_dict(self):
        assert self._c({}) == "{}"


class TestIsSha256Hex:
    def _h(self, v):
        from src.util.audit_log import _is_sha256_hex
        return _is_sha256_hex(v)

    def test_valid_sha256(self):
        h = hashlib.sha256(b"test").hexdigest()
        assert self._h(h) is True

    def test_uppercase_invalid(self):
        h = hashlib.sha256(b"test").hexdigest().upper()
        assert self._h(h) is False  # must be lowercase

    def test_too_short(self):
        assert self._h("abc123") is False

    def test_non_hex_chars(self):
        assert self._h("g" * 64) is False

    def test_none(self):
        assert self._h(None) is False

    def test_int(self):
        assert self._h(12345) is False

    def test_exact_64_hex_lowercase(self):
        assert self._h("a" * 64) is True


class TestAuditLogConstants:
    def test_decision_event_types_non_empty(self):
        from src.util.audit_log import DECISION_EVENT_TYPES
        assert len(DECISION_EVENT_TYPES) > 0

    def test_merge_confirmed_present(self):
        from src.util.audit_log import DECISION_EVENT_TYPES
        assert "merge_confirmed" in DECISION_EVENT_TYPES

    def test_dismiss_identity_candidate_present(self):
        from src.util.audit_log import DECISION_EVENT_TYPES
        assert "dismiss_identity_candidate" in DECISION_EVENT_TYPES

    def test_schema_version_is_int(self):
        from src.util.audit_log import DECISION_EVENT_SCHEMA_VERSION
        assert isinstance(DECISION_EVENT_SCHEMA_VERSION, int)
        assert DECISION_EVENT_SCHEMA_VERSION > 0

    def test_required_fields_non_empty(self):
        from src.util.audit_log import _DECISION_EVENT_REQUIRED_FIELDS
        assert "sha256" in _DECISION_EVENT_REQUIRED_FIELDS
        assert "audit_id" in _DECISION_EVENT_REQUIRED_FIELDS


class TestDecisionLogPath:
    def test_path_contains_year_month(self):
        from src.util.audit_log import _decision_log_path
        dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
        path = _decision_log_path(dt)
        assert "2026-03" in str(path)

    def test_path_ends_with_jsonl(self):
        from src.util.audit_log import _decision_log_path
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert str(_decision_log_path(dt)).endswith(".jsonl")


class TestNormalizeIdempotencyKey:
    def _n(self, v):
        from src.util.audit_log import _normalize_idempotency_key
        return _normalize_idempotency_key(v)

    def test_none_returns_none(self):
        assert self._n(None) is None

    def test_valid_sha256_passthrough_lowercased(self):
        h = hashlib.sha256(b"test").hexdigest()
        assert self._n(h) == h

    def test_non_hex_hashed(self):
        result = self._n("some-readable-key")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._n("key") == self._n("key")

    def test_strips_whitespace(self):
        h = hashlib.sha256(b"test").hexdigest()
        assert self._n("  " + h + "  ") == h


# ---------------------------------------------------------------------------
# api.routes.alerts: _decode_jsonb
# ---------------------------------------------------------------------------

class TestDecodeJsonb:
    def _d(self, raw, default=None):
        from src.api.routes.alerts import _decode_jsonb
        return _decode_jsonb(raw, default)

    def test_none_returns_empty_list(self):
        assert self._d(None) == []

    def test_none_with_default(self):
        assert self._d(None, {}) == {}

    def test_dict_passthrough(self):
        d = {"k": "v"}
        assert self._d(d) is d

    def test_list_passthrough(self):
        lst = [1, 2]
        assert self._d(lst) is lst

    def test_json_string_parsed(self):
        assert self._d('{"a": 1}') == {"a": 1}

    def test_invalid_json_returns_raw_string(self):
        assert self._d("bad-json") == "bad-json"

    def test_json_array_string(self):
        assert self._d('[1, 2, 3]') == [1, 2, 3]

    def test_bytes_json_parsed(self):
        assert self._d(b'{"x": 2}') == {"x": 2}
