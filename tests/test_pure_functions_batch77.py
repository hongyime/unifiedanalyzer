"""
Pure-function tests — batch 77.

Covers:
- api.routes.alerts: _stream_alert_row, _alert_window_row, _decode_alert_suppression
- notifications.alerts: _num, _esc
- api.routes.collector_health: _latest_iso, _row_get, _int_value,
  _blocker_is_active, _parse_datetime
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# api.routes.alerts: _stream_alert_row
# ---------------------------------------------------------------------------

class TestStreamAlertRow:
    def _make_row(self, **kw):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        defaults = dict(
            fingerprint="fp1", alert_type="silence_gap", entity_id="eid-1",
            source="instagram", window_start=dt, window_end=dt,
            last_sent_at=dt, count=3, status="active", detail=None, updated_at=dt,
        )
        defaults.update(kw)
        return defaults

    def _r(self, **kw):
        from src.api.routes.alerts import _stream_alert_row
        return _stream_alert_row(self._make_row(**kw))

    def test_all_keys_present(self):
        r = self._r()
        for k in ("fingerprint", "alert_type", "entity_id", "source",
                  "window_start", "window_end", "last_sent_at", "count",
                  "status", "detail", "updated_at"):
            assert k in r

    def test_datetime_isoformatted(self):
        r = self._r()
        assert "2026-01-01" in r["window_start"]

    def test_none_datetime_stored_as_none(self):
        r = self._r(window_start=None, window_end=None, last_sent_at=None, updated_at=None)
        assert r["window_start"] is None
        assert r["window_end"] is None

    def test_detail_none_becomes_empty_dict(self):
        r = self._r(detail=None)
        assert r["detail"] == {}


# ---------------------------------------------------------------------------
# api.routes.alerts: _alert_window_row
# ---------------------------------------------------------------------------

class TestAlertWindowRow:
    def _make_row(self, **kw):
        dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
        defaults = dict(
            bucket_type="hour", bucket_key="2026-03-15T12", source="telegram",
            window_start=dt, window_end=dt, count=10, baseline=2.5,
            detail=None, created_at=dt, updated_at=dt,
        )
        defaults.update(kw)
        return defaults

    def _r(self, **kw):
        from src.api.routes.alerts import _alert_window_row
        return _alert_window_row(self._make_row(**kw))

    def test_all_keys_present(self):
        r = self._r()
        for k in ("bucket_type", "bucket_key", "source", "window_start",
                  "window_end", "count", "baseline", "detail",
                  "created_at", "updated_at"):
            assert k in r

    def test_baseline_float(self):
        r = self._r(baseline=3)
        assert r["baseline"] == 3.0
        assert isinstance(r["baseline"], float)

    def test_none_baseline_stored_as_none(self):
        assert self._r(baseline=None)["baseline"] is None


# ---------------------------------------------------------------------------
# api.routes.alerts: _decode_alert_suppression
# ---------------------------------------------------------------------------

class TestDecodeAlertSuppression:
    def _make_row(self, **kw):
        dt = datetime(2026, 6, 1, tzinfo=timezone.utc)
        defaults = dict(
            id="sup-1", scope="entity", alert_type="silence_gap",
            entity_id="eid-1", source="instagram", reason="manual",
            starts_at=dt, ends_at=dt, created_at=dt,
        )
        defaults.update(kw)
        return defaults

    def _r(self, **kw):
        from src.api.routes.alerts import _decode_alert_suppression
        return _decode_alert_suppression(self._make_row(**kw))

    def test_all_keys_present(self):
        r = self._r()
        for k in ("id", "scope", "alert_type", "entity_id", "source",
                  "reason", "starts_at", "ends_at", "created_at"):
            assert k in r

    def test_none_ends_at_stored_as_none(self):
        assert self._r(ends_at=None)["ends_at"] is None

    def test_datetime_isoformatted(self):
        r = self._r()
        assert "2026-06-01" in r["starts_at"]


# ---------------------------------------------------------------------------
# notifications.alerts: _num, _esc
# ---------------------------------------------------------------------------

class TestNotifAlertsNum:
    def _n(self, v):
        from src.notifications.alerts import _num
        return _num(v)

    def test_zero(self):
        assert self._n(0) == "0"

    def test_large_number(self):
        assert self._n(1_000_000) == "1,000,000"

    def test_none_returns_zero(self):
        assert self._n(None) == "0"

    def test_invalid_returns_zero(self):
        assert self._n("bad") == "0"


class TestNotifAlertsEsc:
    def _e(self, v):
        from src.notifications.alerts import _esc
        return _esc(v)

    def test_plain_string_unchanged(self):
        assert self._e("hello") == "hello"

    def test_html_chars_escaped(self):
        result = self._e("<b>bold</b>")
        assert "&lt;" in result
        assert "&gt;" in result

    def test_ampersand_escaped(self):
        assert "&amp;" in self._e("a & b")

    def test_non_string_coerced(self):
        assert self._e(42) == "42"


# ---------------------------------------------------------------------------
# api.routes.collector_health: _latest_iso
# ---------------------------------------------------------------------------

class TestLatestIso:
    def _l(self, *values):
        from src.api.routes.collector_health import _latest_iso
        return _latest_iso(*values)

    def test_no_values_returns_none(self):
        assert self._l() is None

    def test_all_none_returns_none(self):
        assert self._l(None, None) is None

    def test_single_value_returned(self):
        assert self._l("2026-01-01T00:00:00+00:00") == "2026-01-01T00:00:00+00:00"

    def test_latest_of_two(self):
        result = self._l("2026-01-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00")
        assert "06-01" in result

    def test_z_suffix_handled(self):
        result = self._l("2026-06-01T12:00:00Z", "2026-01-01T00:00:00Z")
        assert "06-01" in result


# ---------------------------------------------------------------------------
# api.routes.collector_health: _row_get
# ---------------------------------------------------------------------------

class TestRowGet:
    def _g(self, row, key, default=None):
        from src.api.routes.collector_health import _row_get
        return _row_get(row, key, default)

    def test_dict_key_found(self):
        assert self._g({"k": 42}, "k") == 42

    def test_dict_key_missing_returns_default(self):
        assert self._g({}, "k", "fallback") == "fallback"

    def test_none_row_returns_default(self):
        assert self._g(None, "k", 0) == 0

    def test_non_dict_row_returns_default(self):
        assert self._g("not-a-dict", "k", -1) == -1


# ---------------------------------------------------------------------------
# api.routes.collector_health: _int_value
# ---------------------------------------------------------------------------

class TestIntValue:
    def _i(self, v):
        from src.api.routes.collector_health import _int_value
        return _int_value(v)

    def test_none_returns_zero(self):
        assert self._i(None) == 0

    def test_string_int(self):
        assert self._i("5") == 5

    def test_float_truncated(self):
        assert self._i(3.9) == 3

    def test_invalid_returns_zero(self):
        assert self._i("bad") == 0


# ---------------------------------------------------------------------------
# api.routes.collector_health: _blocker_is_active
# ---------------------------------------------------------------------------

class TestBlockerIsActive:
    def _b(self, blocker):
        from src.api.routes.collector_health import _blocker_is_active
        return _blocker_is_active(blocker)

    def test_empty_blocker_not_active(self):
        assert self._b({}) is False

    def test_kind_none_not_active(self):
        assert self._b({"kind": "none", "severity": "ok"}) is False

    def test_kind_error_active(self):
        assert self._b({"kind": "auth_error", "severity": "critical"}) is True

    def test_summary_without_kind_not_active(self):
        # kind='' + severity='' triggers early return False regardless of summary
        assert self._b({"kind": "", "summary": "rate limited"}) is False

    def test_kind_with_summary_active(self):
        # kind non-empty + no matching early-return → True
        assert self._b({"kind": "rate_limit", "summary": "rate limited"}) is True

    def test_next_action_without_kind_not_active(self):
        # kind='' + severity='' → early return False even with next_action
        assert self._b({"next_action": "re-auth"}) is False

    def test_severity_with_next_action_active(self):
        # severity non-empty breaks early return → True
        assert self._b({"severity": "warning", "next_action": "re-auth"}) is True


# ---------------------------------------------------------------------------
# api.routes.collector_health: _parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def _p(self, v):
        from src.api.routes.collector_health import _parse_datetime
        return _parse_datetime(v)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_datetime_aware_returned_utc(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = self._p(dt)
        assert result == dt

    def test_datetime_naive_gets_utc(self):
        dt = datetime(2026, 1, 1)
        result = self._p(dt)
        assert result.tzinfo == timezone.utc

    def test_iso_string_parsed(self):
        result = self._p("2026-06-01T12:00:00+00:00")
        assert result is not None
        assert result.year == 2026

    def test_z_suffix_handled(self):
        result = self._p("2026-06-01T12:00:00Z")
        assert result is not None

    def test_invalid_string_returns_none(self):
        assert self._p("not-a-date") is None
