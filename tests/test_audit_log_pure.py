"""
QA-lane tests for pure helper functions in src/util/audit_log.py.

Covers:
- _canonical_json: deterministic JSON encoding
- _is_sha256_hex: hex string validation
- _normalize_idempotency_key: None passthrough, sha256 passthrough, hash non-sha256
- _clean_str: None/whitespace handling
- _walk_entity_snapshots: recursive snapshot finder
- _hash: SHA-256 chain hash determinism
- _row_value: dict-like row access with default
"""
from __future__ import annotations

import hashlib
import json

import pytest

from src.util.audit_log import (
    _canonical_json,
    _clean_str,
    _hash,
    _is_sha256_hex,
    _normalize_idempotency_key,
    _row_value,
    _walk_entity_snapshots,
)

_VALID_SHA256 = "a" * 64  # 64 lowercase hex chars


# ---------------------------------------------------------------------------
# _canonical_json
# ---------------------------------------------------------------------------

class TestCanonicalJson:
    def test_dict_keys_sorted(self):
        result = _canonical_json({"b": 2, "a": 1})
        parsed = json.loads(result)
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_no_extra_whitespace(self):
        result = _canonical_json({"a": 1})
        assert " " not in result

    def test_same_input_same_output(self):
        obj = {"z": 3, "a": 1, "m": 2}
        assert _canonical_json(obj) == _canonical_json(obj)

    def test_different_key_orders_same_output(self):
        a = {"b": 2, "a": 1}
        b = {"a": 1, "b": 2}
        assert _canonical_json(a) == _canonical_json(b)

    def test_non_serializable_uses_str(self):
        from datetime import datetime, timezone
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = _canonical_json({"ts": ts})
        assert "2024" in result

    def test_list_preserved(self):
        result = _canonical_json([3, 1, 2])
        assert json.loads(result) == [3, 1, 2]


# ---------------------------------------------------------------------------
# _is_sha256_hex
# ---------------------------------------------------------------------------

class TestIsSha256Hex:
    def test_valid_64_char_hex(self):
        assert _is_sha256_hex(_VALID_SHA256) is True

    def test_real_sha256_of_empty_string(self):
        h = hashlib.sha256(b"").hexdigest()
        assert _is_sha256_hex(h) is True

    def test_63_chars_returns_false(self):
        assert _is_sha256_hex("a" * 63) is False

    def test_65_chars_returns_false(self):
        assert _is_sha256_hex("a" * 65) is False

    def test_uppercase_returns_false(self):
        assert _is_sha256_hex("A" * 64) is False

    def test_non_hex_char_returns_false(self):
        assert _is_sha256_hex("g" * 64) is False

    def test_none_returns_false(self):
        assert _is_sha256_hex(None) is False

    def test_empty_string_returns_false(self):
        assert _is_sha256_hex("") is False


# ---------------------------------------------------------------------------
# _normalize_idempotency_key
# ---------------------------------------------------------------------------

class TestNormalizeIdempotencyKey:
    def test_none_returns_none(self):
        assert _normalize_idempotency_key(None) is None

    def test_valid_sha256_returned_as_is(self):
        result = _normalize_idempotency_key(_VALID_SHA256)
        assert result == _VALID_SHA256

    def test_non_sha256_string_hashed(self):
        result = _normalize_idempotency_key("some-key")
        assert result is not None
        assert _is_sha256_hex(result)
        # Should be sha256("some-key")
        expected = hashlib.sha256("some-key".encode()).hexdigest()
        assert result == expected

    def test_strips_and_lowercases_before_check(self):
        # "  " + valid sha256 uppercase → stripped+lowercased → treated as sha256
        key = "  " + _VALID_SHA256.upper() + "  "
        result = _normalize_idempotency_key(key)
        assert result == _VALID_SHA256

    def test_deterministic(self):
        assert _normalize_idempotency_key("test") == _normalize_idempotency_key("test")


# ---------------------------------------------------------------------------
# _clean_str
# ---------------------------------------------------------------------------

class TestCleanStr:
    def test_none_returns_none(self):
        assert _clean_str(None) is None

    def test_empty_string_returns_none(self):
        assert _clean_str("") is None

    def test_whitespace_only_returns_none(self):
        assert _clean_str("   ") is None

    def test_strips_whitespace(self):
        assert _clean_str("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert _clean_str(42) == "42"


# ---------------------------------------------------------------------------
# _walk_entity_snapshots
# ---------------------------------------------------------------------------

class TestWalkEntitySnapshots:
    def test_plain_dict_with_platform_links_yielded(self):
        snapshot = {"platform_links": [{"source": "instagram"}], "name": "Alice"}
        results = list(_walk_entity_snapshots(snapshot))
        assert snapshot in results

    def test_nested_snapshot_found(self):
        data = {"entity": {"platform_links": [{"source": "telegram"}]}}
        results = list(_walk_entity_snapshots(data))
        assert any("platform_links" in r for r in results)

    def test_list_of_snapshots(self):
        data = [
            {"platform_links": [{"source": "a"}]},
            {"platform_links": [{"source": "b"}]},
        ]
        results = list(_walk_entity_snapshots(data))
        assert len(results) == 2

    def test_no_platform_links_nothing_yielded(self):
        data = {"name": "Alice", "age": 30}
        assert list(_walk_entity_snapshots(data)) == []

    def test_empty_dict_yields_nothing(self):
        assert list(_walk_entity_snapshots({})) == []

    def test_empty_list_yields_nothing(self):
        assert list(_walk_entity_snapshots([])) == []


# ---------------------------------------------------------------------------
# _hash
# ---------------------------------------------------------------------------

class TestHash:
    def test_returns_64_char_hex(self):
        result = _hash(None, "merge_entities", None, [], {}, "2024-01-01T00:00:00")
        assert len(result) == 64
        assert _is_sha256_hex(result)

    def test_deterministic(self):
        args = (None, "merge_entities", "user1", ["eid-1"], {"k": "v"}, "2024-01-01T00:00:00")
        assert _hash(*args) == _hash(*args)

    def test_different_actions_different_hash(self):
        h1 = _hash(None, "merge", None, [], {}, "2024-01-01T00:00:00")
        h2 = _hash(None, "split", None, [], {}, "2024-01-01T00:00:00")
        assert h1 != h2

    def test_prev_sha256_affects_hash(self):
        h1 = _hash(None, "merge", None, [], {}, "2024-01-01T00:00:00")
        h2 = _hash(_VALID_SHA256, "merge", None, [], {}, "2024-01-01T00:00:00")
        assert h1 != h2

    def test_entity_ids_sorted_for_stability(self):
        h1 = _hash(None, "act", None, ["b", "a"], {}, "2024-01-01T00:00:00")
        h2 = _hash(None, "act", None, ["a", "b"], {}, "2024-01-01T00:00:00")
        assert h1 == h2


# ---------------------------------------------------------------------------
# _row_value
# ---------------------------------------------------------------------------

class TestRowValue:
    def test_dict_access(self):
        assert _row_value({"k": "v"}, "k") == "v"

    def test_missing_key_returns_default(self):
        assert _row_value({"k": "v"}, "x", "def") == "def"

    def test_none_row_returns_default(self):
        assert _row_value(None, "k", "fallback") == "fallback"

    def test_subscript_object(self):
        class Row:
            def __getitem__(self, k):
                return "val" if k == "key" else (_ for _ in ()).throw(KeyError(k))
        assert _row_value(Row(), "key") == "val"

    def test_subscript_error_falls_back_to_dict_get(self):
        # Not a dict, raises on subscript → returns default
        assert _row_value(object(), "key", "default") == "default"
