"""
Pure-function tests — batch 81.

Covers:
- pipeline.stream_alerts: alert_detail_json, _hour_bucket, _detail_dict,
  _format_window
- pipeline.data_quality_ledger: _iso, _max_dt, _age_seconds
- pipeline.text_normalizer: text_sha1, normalize_social_text, _coerce_json,
  _clean, _domain_from_url, _is_emoji
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: alert_detail_json
# ---------------------------------------------------------------------------

class TestAlertDetailJson:
    def _a(self, **kw):
        from src.pipeline.stream_alerts import alert_detail_json
        return alert_detail_json(**kw)

    def test_returns_json_string(self):
        result = self._a(entity_id="eid-1", count=3)
        parsed = json.loads(result)
        assert parsed["entity_id"] == "eid-1"
        assert parsed["count"] == 3

    def test_none_values_excluded(self):
        result = self._a(entity_id="eid-1", source=None)
        parsed = json.loads(result)
        assert "source" not in parsed

    def test_empty_kwargs_returns_empty_obj(self):
        assert json.loads(self._a()) == {}


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: _hour_bucket
# ---------------------------------------------------------------------------

class TestHourBucket:
    def _h(self, dt):
        from src.pipeline.stream_alerts import _hour_bucket
        return _hour_bucket(dt)

    def test_start_at_hour_boundary(self):
        dt = datetime(2026, 6, 1, 14, 37, 22, tzinfo=timezone.utc)
        start, end = self._h(dt)
        assert start.minute == 0
        assert start.second == 0
        assert start.hour == 14

    def test_end_is_one_hour_later(self):
        dt = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
        start, end = self._h(dt)
        assert (end - start).seconds == 3600


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: _detail_dict
# ---------------------------------------------------------------------------

class TestDetailDict:
    def _d(self, v):
        from src.pipeline.stream_alerts import _detail_dict
        return _detail_dict(v)

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._d(d) is d

    def test_json_string_parsed(self):
        assert self._d(json.dumps({"x": 5})) == {"x": 5}

    def test_invalid_json_returns_empty(self):
        assert self._d("not json") == {}

    def test_empty_string_returns_empty(self):
        assert self._d("") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}

    def test_json_array_returns_empty(self):
        assert self._d(json.dumps([1, 2, 3])) == {}


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: _format_window
# ---------------------------------------------------------------------------

class TestFormatWindow:
    def _f(self, v):
        from src.pipeline.stream_alerts import _format_window
        return _format_window(v)

    def test_datetime_formatted(self):
        dt = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
        result = self._f(dt)
        assert "2026-06-01" in result
        assert "UTC" in result

    def test_iso_string_formatted(self):
        result = self._f("2026-06-01T14:30:00+00:00")
        assert "2026-06-01" in result

    def test_none_returns_unknown(self):
        assert self._f(None) == "unknown"

    def test_invalid_string_returned_as_is(self):
        result = self._f("not-a-date")
        assert result == "not-a-date"


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _iso
# ---------------------------------------------------------------------------

class TestDqlIso:
    def _i(self, v):
        from src.pipeline.data_quality_ledger import _iso
        return _iso(v)

    def test_datetime_isoformatted(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        assert self._i(dt) == dt.isoformat()

    def test_none_returns_none(self):
        assert self._i(None) is None

    def test_no_isoformat_returns_none(self):
        assert self._i(42) is None


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _max_dt
# ---------------------------------------------------------------------------

class TestMaxDt:
    def _m(self, values):
        from src.pipeline.data_quality_ledger import _max_dt
        return _max_dt(values)

    def test_empty_returns_none(self):
        assert self._m([]) is None

    def test_all_none_returns_none(self):
        assert self._m([None, None]) is None

    def test_max_of_datetimes(self):
        dt1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        assert self._m([dt1, dt2]) == dt2

    def test_ignores_none(self):
        dt = datetime(2026, 3, 1, tzinfo=timezone.utc)
        assert self._m([None, dt, None]) == dt


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _age_seconds
# ---------------------------------------------------------------------------

class TestDqlAgeSeconds:
    def _a(self, value):
        from src.pipeline.data_quality_ledger import _age_seconds
        return _age_seconds(value)

    def test_none_returns_none(self):
        assert self._a(None) is None

    def test_recent_datetime_small_age(self):
        dt = datetime.now(timezone.utc) - timedelta(seconds=10)
        result = self._a(dt)
        assert result is not None
        assert 0 <= result <= 30

    def test_iso_string_parsed(self):
        dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = self._a(dt.isoformat())
        assert result is not None
        assert result >= 280

    def test_invalid_string_returns_none(self):
        assert self._a("not-a-date") is None

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime.utcnow() - timedelta(minutes=1)
        result = self._a(dt)
        assert result is not None


# ---------------------------------------------------------------------------
# pipeline.text_normalizer: text_sha1
# ---------------------------------------------------------------------------

class TestTextSha1:
    def _s(self, text):
        from src.pipeline.text_normalizer import text_sha1
        return text_sha1(text)

    def test_returns_40_char_hex(self):
        result = self._s("hello")
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._s("hello") == self._s("hello")

    def test_empty_string(self):
        result = self._s("")
        assert len(result) == 40

    def test_none_handled(self):
        result = self._s(None)
        assert len(result) == 40


# ---------------------------------------------------------------------------
# pipeline.text_normalizer: normalize_social_text
# ---------------------------------------------------------------------------

class TestNormalizeSocialText:
    def _n(self, text, max_chars=8000):
        from src.pipeline.text_normalizer import normalize_social_text
        return normalize_social_text(text, max_chars=max_chars)

    def test_none_returns_empty(self):
        assert self._n(None) == ""

    def test_strips_whitespace(self):
        assert self._n("  hello  ") == "hello"

    def test_multiple_newlines_collapsed(self):
        result = self._n("a\n\n\n\nb")
        assert "\n\n\n" not in result

    def test_truncates_to_max_chars(self):
        assert len(self._n("x" * 200, max_chars=100)) <= 100

    def test_max_chars_zero_no_truncation(self):
        text = "x" * 200
        assert len(self._n(text, max_chars=0)) == 200


# ---------------------------------------------------------------------------
# pipeline.text_normalizer: _coerce_json
# ---------------------------------------------------------------------------

class TestCoerceJson:
    def _c(self, v):
        from src.pipeline.text_normalizer import _coerce_json
        return _coerce_json(v)

    def test_none_returns_empty(self):
        assert self._c(None) == {}

    def test_dict_returned_as_dict(self):
        d = {"k": 1}
        assert self._c(d) == {"k": 1}

    def test_json_string_parsed(self):
        assert self._c(json.dumps({"x": 5})) == {"x": 5}

    def test_invalid_json_wraps_in_raw(self):
        result = self._c("not json")
        assert "raw" in result

    def test_list_wraps_in_raw(self):
        result = self._c([1, 2])
        assert "raw" in result


# ---------------------------------------------------------------------------
# pipeline.text_normalizer: _clean
# ---------------------------------------------------------------------------

class TestTextNormalizerClean:
    def _c(self, v):
        from src.pipeline.text_normalizer import _clean
        return _clean(v)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_empty_returns_none(self):
        assert self._c("") is None

    def test_strips_and_returns(self):
        assert self._c("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert self._c(99) == "99"


# ---------------------------------------------------------------------------
# pipeline.text_normalizer: _domain_from_url
# ---------------------------------------------------------------------------

class TestDomainFromUrl:
    def _d(self, url):
        from src.pipeline.text_normalizer import _domain_from_url
        return _domain_from_url(url)

    def test_valid_url_strips_www(self):
        result = self._d("https://www.example.com/path")
        assert result == "example.com"

    def test_http_url(self):
        result = self._d("http://example.org")
        assert result == "example.org"

    def test_empty_string_returns_none(self):
        assert self._d("") is None

    def test_none_returns_none(self):
        assert self._d(None) is None
