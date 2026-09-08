"""
Pure-function tests — batch 76.

Covers:
- api.routes.data_quality: _cache_ttl_seconds, _parse_ts, _cache_age_seconds
- api.routes.alerts: _decode_jsonb
- api.routes.export: _hash_value
- notifications.intelligence: intelligence_run_lines
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os


# ---------------------------------------------------------------------------
# api.routes.data_quality: _cache_ttl_seconds
# ---------------------------------------------------------------------------

class TestCacheTtlSeconds:
    def _t(self, value=None):
        from src.api.routes.data_quality import _cache_ttl_seconds
        key = "ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS"
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _cache_ttl_seconds()
        finally:
            os.environ.pop(key, None)

    def test_default_900(self):
        assert self._t() == 900

    def test_custom_value(self):
        assert self._t(300) == 300

    def test_zero_clamped(self):
        assert self._t(0) == 0

    def test_negative_clamped_to_zero(self):
        assert self._t(-1) == 0

    def test_invalid_string_returns_default(self):
        assert self._t("notanint") == 900


# ---------------------------------------------------------------------------
# api.routes.data_quality: _parse_ts
# ---------------------------------------------------------------------------

class TestParseTs:
    def _p(self, value):
        from src.api.routes.data_quality import _parse_ts
        return _parse_ts(value)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_empty_string_returns_none(self):
        assert self._p("") is None

    def test_non_string_returns_none(self):
        assert self._p(12345) is None

    def test_valid_iso_utc(self):
        result = self._p("2026-06-01T12:00:00+00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_valid_iso_z(self):
        result = self._p("2026-06-01T12:00:00Z")
        assert result is not None

    def test_naive_iso_gets_utc(self):
        result = self._p("2026-06-01T12:00:00")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_invalid_format_returns_none(self):
        assert self._p("not-a-date") is None


# ---------------------------------------------------------------------------
# api.routes.data_quality: _cache_age_seconds
# ---------------------------------------------------------------------------

class TestCacheAgeSeconds:
    def _a(self, payload):
        from src.api.routes.data_quality import _cache_age_seconds
        return _cache_age_seconds(payload)

    def test_missing_generated_at_returns_none(self):
        assert self._a({}) is None

    def test_invalid_generated_at_returns_none(self):
        assert self._a({"generated_at": "not-a-date"}) is None

    def test_recent_timestamp_small_age(self):
        dt = datetime.now(timezone.utc) - timedelta(seconds=10)
        result = self._a({"generated_at": dt.isoformat()})
        assert result is not None
        assert 0 <= result <= 30

    def test_old_timestamp_large_age(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=2)
        result = self._a({"generated_at": dt.isoformat()})
        assert result is not None
        assert result >= 7000


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
        assert self._d(None, default={}) == {}

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._d(d) is d

    def test_list_passthrough(self):
        lst = [1, 2, 3]
        assert self._d(lst) is lst

    def test_json_string_parsed(self):
        import json
        result = self._d(json.dumps({"x": 42}))
        assert result == {"x": 42}

    def test_json_bytes_parsed(self):
        import json
        result = self._d(json.dumps({"y": 7}).encode())
        assert result == {"y": 7}

    def test_invalid_json_string_returned_raw(self):
        result = self._d("not valid json")
        assert result == "not valid json"

    def test_integer_returned_raw(self):
        assert self._d(42) == 42


# ---------------------------------------------------------------------------
# api.routes.export: _hash_value
# ---------------------------------------------------------------------------

class TestHashValue:
    def _h(self, value):
        from src.api.routes.export import _hash_value
        return _hash_value(value)

    def test_none_returns_none(self):
        assert self._h(None) is None

    def test_empty_string_returns_none(self):
        assert self._h("") is None

    def test_returns_16_char_hex(self):
        result = self._h("test@example.com")
        assert result is not None
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._h("foo@bar.com") == self._h("foo@bar.com")

    def test_different_inputs_different_hashes(self):
        assert self._h("a@b.com") != self._h("c@d.com")


# ---------------------------------------------------------------------------
# notifications.intelligence: intelligence_run_lines
# ---------------------------------------------------------------------------

class TestIntelligenceRunLines:
    def _r(self, stats):
        from src.notifications.intelligence import intelligence_run_lines
        return intelligence_run_lines(stats)

    def test_all_zeros_returns_empty(self):
        assert self._r({}) == []

    def test_with_text_features_returns_line(self):
        lines = self._r({"text_features": 100})
        assert len(lines) >= 1
        assert "100" in lines[0]

    def test_with_alerts_included(self):
        lines = self._r({"alerts": 5})
        assert any("5" in l for l in lines)

    def test_spike_alert_breakdown(self):
        stats = {
            "text_features": 10,
            "alert_breakdown": {"emotional_spike": 3},
        }
        lines = self._r(stats)
        assert len(lines) <= 2
        spike_line = " ".join(lines)
        assert "emotional" in spike_line or "3" in spike_line

    def test_max_two_lines(self):
        stats = {
            "text_features": 10,
            "alerts": 5,
            "alert_breakdown": {"emotional_spike": 2, "location_evidence_spike": 1},
        }
        assert len(self._r(stats)) <= 2
