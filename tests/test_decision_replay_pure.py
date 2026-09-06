"""
QA-lane tests for pure helper functions in src/pipeline/decision_replay.py:
- _clean, _coerce_feature_snapshot, _features_from_snapshot_container,
  summarize_payload, stable_refs_from_event, _canonical_event_type,
  _walk_snapshots, _uuid_list_or_none, _as_aware_datetime, _row_value
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from src.pipeline.decision_replay import (
    _as_aware_datetime,
    _canonical_event_type,
    _clean,
    _coerce_feature_snapshot,
    _features_from_snapshot_container,
    _row_value,
    _uuid_list_or_none,
    _walk_snapshots,
    stable_refs_from_event,
    summarize_payload,
)


# ---------------------------------------------------------------------------
# _clean
# ---------------------------------------------------------------------------

class TestDecisionReplayClean:
    def test_none_returns_none(self):
        assert _clean(None) is None

    def test_empty_returns_none(self):
        assert _clean("") is None

    def test_whitespace_returns_none(self):
        assert _clean("   ") is None

    def test_strips_and_returns(self):
        assert _clean("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert _clean(42) == "42"


# ---------------------------------------------------------------------------
# _coerce_feature_snapshot
# ---------------------------------------------------------------------------

class TestCoerceFeatureSnapshot:
    def test_empty_returns_empty(self):
        assert _coerce_feature_snapshot({}) == {}

    def test_float_values_coerced(self):
        result = _coerce_feature_snapshot({"email_match": 0.9, "username_exact": 0.7})
        assert abs(result["email_match"] - 0.9) < 1e-9

    def test_string_float_coerced(self):
        result = _coerce_feature_snapshot({"k": "0.5"})
        assert abs(result["k"] - 0.5) < 1e-9

    def test_invalid_values_skipped(self):
        result = _coerce_feature_snapshot({"k": "bad", "good": 0.8})
        assert "k" not in result
        assert abs(result["good"] - 0.8) < 1e-9

    def test_none_value_treated_as_zero(self):
        result = _coerce_feature_snapshot({"k": None})
        assert result["k"] == 0.0


# ---------------------------------------------------------------------------
# _features_from_snapshot_container
# ---------------------------------------------------------------------------

class TestFeaturesFromSnapshotContainer:
    def test_empty_returns_empty(self):
        assert _features_from_snapshot_container({}) == {}

    def test_non_dict_returns_empty(self):
        assert _features_from_snapshot_container("string") == {}

    def test_dict_contributing_signals(self):
        # contributing_signals as a dict routes through _coerce_feature_snapshot
        snapshot = {"sources": {"contributing_signals": {"email_match": 0.9}}}
        result = _features_from_snapshot_container(snapshot)
        assert abs(result.get("email_match", 0) - 0.9) < 1e-9

    def test_list_signals(self):
        snapshot = {"sources": {"contributing_signals": [
            {"type": "email_match", "confidence": 0.9},
            {"type": "username_exact", "confidence": 0.7},
        ]}}
        result = _features_from_snapshot_container(snapshot)
        assert abs(result.get("email_match", 0) - 0.9) < 1e-9

    def test_takes_max_confidence_per_type(self):
        snapshot = {"sources": {"contributing_signals": [
            {"type": "email_match", "confidence": 0.5},
            {"type": "email_match", "confidence": 0.9},
        ]}}
        result = _features_from_snapshot_container(snapshot)
        assert abs(result.get("email_match", 0) - 0.9) < 1e-9


# ---------------------------------------------------------------------------
# summarize_payload
# ---------------------------------------------------------------------------

class TestSummarizePayload:
    def test_empty_returns_empty(self):
        assert summarize_payload({}) == {}

    def test_known_keys_extracted(self):
        payload = {"watch_status": "primary", "confidence": 0.9, "other_key": "ignored"}
        result = summarize_payload(payload)
        assert result["watch_status"] == "primary"
        assert abs(result["confidence"] - 0.9) < 1e-9
        assert "other_key" not in result

    def test_long_string_truncated(self):
        payload = {"notes": "x" * 200}
        result = summarize_payload(payload)
        assert len(result["notes"]) <= 160
        assert result["notes"].endswith("...")


# ---------------------------------------------------------------------------
# stable_refs_from_event
# ---------------------------------------------------------------------------

class TestStableRefsFromEvent:
    def test_empty_event_returns_empty(self):
        assert stable_refs_from_event({}) == []

    def test_stable_refs_extracted(self):
        event = {"stable_refs": [
            {"source": "instagram", "platform_id": "123", "platform_username": "alice"},
        ], "payload": {}}
        result = stable_refs_from_event(event)
        assert len(result) == 1
        assert result[0]["source"] == "instagram"

    def test_missing_platform_id_and_username_skipped(self):
        event = {"stable_refs": [
            {"source": "instagram", "platform_id": None, "platform_username": None},
        ], "payload": {}}
        result = stable_refs_from_event(event)
        assert result == []

    def test_deduplicates_refs(self):
        event = {"stable_refs": [
            {"source": "instagram", "platform_id": "123", "platform_username": None},
            {"source": "instagram", "platform_id": "123", "platform_username": None},
        ], "payload": {}}
        result = stable_refs_from_event(event)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _canonical_event_type
# ---------------------------------------------------------------------------

class TestCanonicalEventType:
    def test_none_returns_none(self):
        assert _canonical_event_type(None) is None

    def test_known_event_type_returned(self):
        result = _canonical_event_type("merge_entities")
        assert isinstance(result, str)

    def test_unknown_returned_as_is(self):
        assert _canonical_event_type("custom_event") == "custom_event"


# ---------------------------------------------------------------------------
# _walk_snapshots
# ---------------------------------------------------------------------------

class TestWalkSnapshots:
    def test_dict_with_platform_links_yielded(self):
        snapshot = {"platform_links": [{"source": "instagram"}], "name": "Alice"}
        results = list(_walk_snapshots(snapshot))
        assert snapshot in results

    def test_nested_snapshot_found(self):
        data = {"entity": {"platform_links": [{"source": "telegram"}]}}
        results = list(_walk_snapshots(data))
        assert any("platform_links" in r for r in results)

    def test_empty_dict_yields_nothing(self):
        assert list(_walk_snapshots({})) == []


# ---------------------------------------------------------------------------
# _uuid_list_or_none
# ---------------------------------------------------------------------------

class TestUuidListOrNone:
    def test_empty_returns_none(self):
        assert _uuid_list_or_none([]) is None

    def test_valid_uuids_normalized(self):
        uid = "00000000-0000-0000-0000-000000000001"
        result = _uuid_list_or_none([uid])
        assert result == [uid]

    def test_compact_uuid_normalized(self):
        result = _uuid_list_or_none(["00000000000000000000000000000001"])
        assert result is not None
        assert "-" in result[0]


# ---------------------------------------------------------------------------
# _as_aware_datetime
# ---------------------------------------------------------------------------

class TestAsAwareDatetime:
    def test_none_returns_none(self):
        assert _as_aware_datetime(None) is None

    def test_empty_returns_none(self):
        assert _as_aware_datetime("") is None

    def test_datetime_with_tz_returned_utc(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        result = _as_aware_datetime(ts)
        assert result is not None
        assert result.tzinfo is not None

    def test_iso_string_parsed(self):
        result = _as_aware_datetime("2024-06-01T12:00:00+00:00")
        assert result is not None
        assert result.year == 2024

    def test_naive_datetime_gets_utc(self):
        ts = datetime(2024, 6, 1, 12, 0)
        result = _as_aware_datetime(ts)
        assert result is not None
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# _row_value
# ---------------------------------------------------------------------------

class TestDecisionReplayRowValue:
    def test_dict_access(self):
        assert _row_value({"k": "v"}, "k") == "v"

    def test_missing_key_returns_none(self):
        assert _row_value({"k": "v"}, "x") is None

    def test_subscript_object(self):
        class Row:
            def __getitem__(self, k): return "val"
        assert _row_value(Row(), "key") == "val"
