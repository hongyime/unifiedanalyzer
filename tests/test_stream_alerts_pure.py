"""
QA-lane tests for pure functions in src/pipeline/stream_alerts.py.

Covers:
- make_alert_fingerprint: deterministic SHA256, field pass-through
- is_suppressed: wildcard match, type/entity/source/time filtering
- alert_detail_json: None exclusion, serialization
- _hour_bucket: start/end alignment
- _detail_dict: dict/str/invalid coercion
- _format_window: datetime formatting, ISO string parsing
- burst_alert_type_for_event_type: message/media/unknown types
- collector_resume_from_status: stale/degraded → fresh transitions
- emotional_z_score: basic z-score, zero-stddev floor
- parse_cursor_datetime: datetime, ISO string, fallback, missing raises
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.pipeline.stream_alerts import (
    _detail_dict,
    _format_window,
    _hour_bucket,
    alert_detail_json,
    burst_alert_type_for_event_type,
    collector_resume_from_status,
    emotional_z_score,
    is_suppressed,
    make_alert_fingerprint,
    parse_cursor_datetime,
)

_NOW = datetime(2026, 9, 5, 14, 30, 0, tzinfo=timezone.utc)
_ENTITY = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# make_alert_fingerprint
# ---------------------------------------------------------------------------

class TestMakeAlertFingerprint:
    def _make(self, **kwargs):
        defaults = dict(
            alert_type="SILENCE_GAP",
            entity_id=_ENTITY,
            source="telegram",
            bucket_key="2026-09-05T14",
            window_start=_NOW,
            window_end=_NOW + timedelta(hours=1),
        )
        defaults.update(kwargs)
        return make_alert_fingerprint(**defaults)

    def test_returns_fingerprint_object(self):
        fp = self._make()
        assert fp.fingerprint
        assert len(fp.fingerprint) == 64  # SHA256 hex

    def test_deterministic(self):
        fp1 = self._make()
        fp2 = self._make()
        assert fp1.fingerprint == fp2.fingerprint

    def test_different_inputs_different_fingerprints(self):
        fp1 = self._make(alert_type="SILENCE_GAP")
        fp2 = self._make(alert_type="NEW_ACTIVITY")
        assert fp1.fingerprint != fp2.fingerprint

    def test_fields_preserved(self):
        fp = self._make()
        assert fp.alert_type == "SILENCE_GAP"
        assert fp.entity_id == _ENTITY
        assert fp.source == "telegram"

    def test_none_entity_allowed(self):
        fp = self._make(entity_id=None)
        assert fp.entity_id is None
        assert fp.fingerprint  # still produces valid fingerprint


# ---------------------------------------------------------------------------
# is_suppressed
# ---------------------------------------------------------------------------

class TestIsSuppressed:
    def test_empty_suppressions_returns_false(self):
        assert is_suppressed([], alert_type="X", entity_id=_ENTITY, source="tg", now=_NOW) is False

    def test_exact_match_suppresses(self):
        row = {"alert_type": "SILENCE_GAP", "entity_id": _ENTITY, "source": "telegram",
               "starts_at": None, "ends_at": None}
        assert is_suppressed([row], alert_type="SILENCE_GAP", entity_id=_ENTITY,
                              source="telegram", now=_NOW) is True

    def test_wildcard_alert_type_suppresses(self):
        row = {"alert_type": "*", "entity_id": None, "source": None,
               "starts_at": None, "ends_at": None}
        assert is_suppressed([row], alert_type="ANY_TYPE", entity_id=_ENTITY,
                              source="tg", now=_NOW) is True

    def test_wrong_alert_type_does_not_suppress(self):
        row = {"alert_type": "OTHER", "entity_id": None, "source": None,
               "starts_at": None, "ends_at": None}
        assert is_suppressed([row], alert_type="SILENCE_GAP", entity_id=_ENTITY,
                              source="tg", now=_NOW) is False

    def test_future_start_does_not_suppress(self):
        row = {"alert_type": None, "entity_id": None, "source": None,
               "starts_at": _NOW + timedelta(hours=1), "ends_at": None}
        assert is_suppressed([row], alert_type="X", entity_id=None,
                              source=None, now=_NOW) is False

    def test_expired_end_does_not_suppress(self):
        row = {"alert_type": None, "entity_id": None, "source": None,
               "starts_at": None, "ends_at": _NOW - timedelta(hours=1)}
        assert is_suppressed([row], alert_type="X", entity_id=None,
                              source=None, now=_NOW) is False


# ---------------------------------------------------------------------------
# alert_detail_json
# ---------------------------------------------------------------------------

class TestAlertDetailJson:
    def test_serializes_kwargs(self):
        import json
        result = json.loads(alert_detail_json(count=5, source="telegram"))
        assert result["count"] == 5
        assert result["source"] == "telegram"

    def test_none_values_excluded(self):
        import json
        result = json.loads(alert_detail_json(count=5, missing=None))
        assert "missing" not in result

    def test_empty_returns_empty_object(self):
        import json
        assert json.loads(alert_detail_json()) == {}

    def test_non_serializable_uses_str(self):
        import json
        result = json.loads(alert_detail_json(ts=_NOW))
        assert "ts" in result


# ---------------------------------------------------------------------------
# _hour_bucket
# ---------------------------------------------------------------------------

class TestHourBucket:
    def test_start_is_on_the_hour(self):
        ts = datetime(2024, 6, 1, 14, 37, 22, tzinfo=timezone.utc)
        start, end = _hour_bucket(ts)
        assert start.minute == 0
        assert start.second == 0
        assert start.hour == 14

    def test_end_is_one_hour_after_start(self):
        ts = datetime(2024, 6, 1, 14, 37, tzinfo=timezone.utc)
        start, end = _hour_bucket(ts)
        assert end == start + timedelta(hours=1)

    def test_on_the_hour_input(self):
        ts = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        start, _ = _hour_bucket(ts)
        assert start == ts


# ---------------------------------------------------------------------------
# _detail_dict
# ---------------------------------------------------------------------------

class TestDetailDict:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert _detail_dict(d) == d

    def test_valid_json_string_parsed(self):
        assert _detail_dict('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_empty(self):
        assert _detail_dict("bad{json") == {}

    def test_none_returns_empty(self):
        assert _detail_dict(None) == {}

    def test_empty_string_returns_empty(self):
        assert _detail_dict("") == {}


# ---------------------------------------------------------------------------
# _format_window
# ---------------------------------------------------------------------------

class TestFormatWindow:
    def test_datetime_formatted(self):
        ts = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        result = _format_window(ts)
        assert "2024-06-01" in result
        assert "14:30" in result

    def test_iso_string_parsed_and_formatted(self):
        result = _format_window("2024-06-01T14:30:00+00:00")
        assert "2024-06-01" in result

    def test_invalid_string_returned_as_is(self):
        result = _format_window("not-a-date")
        assert result == "not-a-date"

    def test_none_returns_unknown(self):
        result = _format_window(None)
        assert result == "unknown"


# ---------------------------------------------------------------------------
# burst_alert_type_for_event_type
# ---------------------------------------------------------------------------

class TestBurstAlertTypeForEventType:
    def test_message_event_type(self):
        assert burst_alert_type_for_event_type("MESSAGE_SENT") == "MESSAGE_BURST"

    def test_media_event_type(self):
        assert burst_alert_type_for_event_type("CONTENT_PUBLISHED") == "MEDIA_BURST"

    def test_unknown_returns_none(self):
        assert burst_alert_type_for_event_type("CODE_COMMIT") is None

    def test_none_returns_none(self):
        assert burst_alert_type_for_event_type(None) is None


# ---------------------------------------------------------------------------
# collector_resume_from_status
# ---------------------------------------------------------------------------

class TestCollectorResumeFromStatus:
    def test_stale_to_fresh_returns_true(self):
        assert collector_resume_from_status("stale", "fresh") is True

    def test_degraded_to_fresh_returns_true(self):
        assert collector_resume_from_status("degraded", "fresh") is True

    def test_fresh_to_fresh_returns_false(self):
        assert collector_resume_from_status("fresh", "fresh") is False

    def test_none_to_fresh_returns_false(self):
        assert collector_resume_from_status(None, "fresh") is False

    def test_stale_to_stale_returns_false(self):
        assert collector_resume_from_status("stale", "stale") is False


# ---------------------------------------------------------------------------
# emotional_z_score
# ---------------------------------------------------------------------------

class TestEmotionalZScore:
    def test_basic_z_score(self):
        z = emotional_z_score(current_mean=1.5, baseline_mean=1.0, baseline_stddev=0.5)
        assert abs(z - 1.0) < 1e-9

    def test_zero_stddev_uses_floor(self):
        # stddev=0 → floor 0.05
        z = emotional_z_score(current_mean=1.1, baseline_mean=1.0, baseline_stddev=0.0)
        assert abs(z - 2.0) < 1e-9  # (1.1-1.0)/0.05 = 2.0

    def test_none_stddev_uses_floor(self):
        z = emotional_z_score(current_mean=1.1, baseline_mean=1.0, baseline_stddev=None)
        assert abs(z - 2.0) < 1e-9

    def test_negative_z_when_below_baseline(self):
        z = emotional_z_score(current_mean=0.5, baseline_mean=1.0, baseline_stddev=0.5)
        assert z < 0

    def test_zero_when_equal(self):
        z = emotional_z_score(current_mean=1.0, baseline_mean=1.0, baseline_stddev=1.0)
        assert z == 0.0


# ---------------------------------------------------------------------------
# parse_cursor_datetime
# ---------------------------------------------------------------------------

class TestParseCursorDatetime:
    def test_datetime_passthrough(self):
        result = parse_cursor_datetime(_NOW)
        assert result == _NOW

    def test_iso_string_parsed(self):
        result = parse_cursor_datetime("2024-06-01T14:30:00+00:00")
        assert result.year == 2024

    def test_z_suffix_handled(self):
        result = parse_cursor_datetime("2024-06-01T14:30:00Z")
        assert result.tzinfo is not None

    def test_fallback_used_for_none(self):
        result = parse_cursor_datetime(None, fallback=_NOW)
        assert result == _NOW

    def test_fallback_used_for_empty_string(self):
        result = parse_cursor_datetime("", fallback=_NOW)
        assert result == _NOW

    def test_raises_when_no_fallback(self):
        with pytest.raises(ValueError):
            parse_cursor_datetime(None)
