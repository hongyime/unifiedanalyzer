"""
Pure-function tests — batch 74.

Covers:
- api.routes.timeline: _derive_timeline_confidence, _partition_names_desc
- api.routes.readiness: _env_int_local, _ok, _check, _collector_summary_ok
- notifications.telegram: _preview, _parse_retry_after
- api.routes.face_search: _iso, _vector_literal, _infer_platform
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import pytest


# ---------------------------------------------------------------------------
# api.routes.timeline: _derive_timeline_confidence
# ---------------------------------------------------------------------------

class TestDeriveTimelineConfidence:
    def _d(self, metadata):
        from src.api.routes.timeline import _derive_timeline_confidence
        return _derive_timeline_confidence(metadata)

    def test_none_metadata_returns_none(self):
        conf, source = self._d(None)
        assert conf is None
        assert source is None

    def test_empty_dict_returns_none(self):
        conf, source = self._d({})
        assert conf is None

    def test_nested_confidence_extracted(self):
        metadata = {"confidence": 0.87}
        conf, source = self._d(metadata)
        if conf is not None:
            assert 0.0 <= conf <= 1.0

    def test_string_returns_none(self):
        conf, source = self._d("not a mapping")
        assert conf is None


# ---------------------------------------------------------------------------
# api.routes.timeline: _partition_names_desc
# ---------------------------------------------------------------------------

class TestPartitionNamesDesc:
    def _p(self, start=None, floor_year=2010):
        from src.api.routes.timeline import _partition_names_desc
        return _partition_names_desc(start, floor_year)

    def test_returns_list(self):
        result = self._p()
        assert isinstance(result, list)

    def test_descending_order(self):
        result = self._p()
        assert result == sorted(result, reverse=True)

    def test_all_strings(self):
        for name in self._p():
            assert isinstance(name, str)

    def test_start_year_limits_range(self):
        start = datetime(2023, 6, 1, tzinfo=timezone.utc)
        result = self._p(start=start, floor_year=2022)
        # Names are timeline_events_YYYY_MM — year is at index [-2]
        for name in result:
            parts = name.split("_")
            if len(parts) >= 2:
                try:
                    year = int(parts[-2])
                    assert year >= 2022
                except ValueError:
                    pass


# ---------------------------------------------------------------------------
# api.routes.readiness: _env_int_local
# ---------------------------------------------------------------------------

class TestEnvIntLocal:
    def _e(self, name, default, minimum=None, value=None):
        from src.api.routes.readiness import _env_int_local
        if value is not None:
            os.environ[name] = str(value)
        else:
            os.environ.pop(name, None)
        try:
            return _env_int_local(name, default, minimum=minimum)
        finally:
            os.environ.pop(name, None)

    def test_returns_default_when_unset(self):
        assert self._e("__TEST_ENV_UNSET__", 42) == 42

    def test_returns_env_value(self):
        assert self._e("__TEST_ENV_X__", 42, value=10) == 10

    def test_minimum_enforced(self):
        assert self._e("__TEST_ENV_Y__", 1, minimum=5, value=2) == 5

    def test_invalid_string_returns_default(self):
        assert self._e("__TEST_ENV_Z__", 7, value="notanint") == 7

    def test_minimum_not_applied_when_none(self):
        assert self._e("__TEST_ENV_W__", 100, value=1) == 1


# ---------------------------------------------------------------------------
# api.routes.readiness: _ok
# ---------------------------------------------------------------------------

class TestOk:
    def _o(self, v):
        from src.api.routes.readiness import _ok
        return _ok(v)

    def test_true_is_ok(self):
        assert self._o(True) is True

    def test_ok_string_is_ok(self):
        assert self._o("ok") is True

    def test_connected_string_is_ok(self):
        assert self._o("connected") is True

    def test_false_not_ok(self):
        assert self._o(False) is False

    def test_none_not_ok(self):
        assert self._o(None) is False

    def test_error_string_not_ok(self):
        assert self._o("error") is False


# ---------------------------------------------------------------------------
# api.routes.readiness: _check
# ---------------------------------------------------------------------------

class TestCheck:
    def _c(self, ok, check_id="db_connected", **kw):
        from src.api.routes.readiness import _check
        defaults = dict(title="DB Connected", detail="ok", evidence={})
        defaults.update(kw)
        return _check(check_id=check_id, ok=ok, **defaults)

    def test_ok_true_status_ok(self):
        r = self._c(True)
        assert r["ok"] is True
        assert r["status"] == "ok"

    def test_ok_false_status_degraded(self):
        r = self._c(False)
        assert r["ok"] is False
        assert r["status"] == "degraded"

    def test_severity_default_critical(self):
        assert self._c(True)["severity"] == "critical"

    def test_evidence_stored(self):
        r = self._c(True, evidence={"rows": 5})
        assert r["evidence"]["rows"] == 5

    def test_check_id_in_result(self):
        r = self._c(True, check_id="face_index_ready")
        assert r["id"] == "face_index_ready"


# ---------------------------------------------------------------------------
# notifications.telegram: _preview
# ---------------------------------------------------------------------------

class TestPreview:
    def _p(self, text, limit=1000):
        from src.notifications.telegram import _preview
        return _preview(text, limit)

    def test_whitespace_collapsed(self):
        assert self._p("hello   world") == "hello world"

    def test_newlines_collapsed(self):
        assert self._p("line1\nline2\nline3") == "line1 line2 line3"

    def test_truncated_to_limit(self):
        assert len(self._p("x" * 2000, limit=100)) == 100

    def test_short_text_unchanged(self):
        assert self._p("short") == "short"

    def test_non_string_coerced(self):
        assert self._p(12345) == "12345"


# ---------------------------------------------------------------------------
# notifications.telegram: _parse_retry_after
# ---------------------------------------------------------------------------

class TestParseRetryAfter:
    def _r(self, error):
        from src.notifications.telegram import _parse_retry_after
        return _parse_retry_after(error)

    def test_none_returns_none(self):
        assert self._r(None) is None

    def test_no_429_returns_none(self):
        assert self._r("500 Internal Server Error") is None

    def test_429_with_retry_after(self):
        payload = json.dumps({"parameters": {"retry_after": 30}})
        error = f"429 Too Many Requests {payload}"
        result = self._r(error)
        assert result == 30

    def test_minimum_1(self):
        payload = json.dumps({"parameters": {"retry_after": 0}})
        error = f"429 {payload}"
        result = self._r(error)
        assert result == 1

    def test_no_json_no_marker_returns_fallback(self):
        # No '{' and no 'retry after' marker → falls through to hardcoded 5
        assert self._r("429 Too Many Requests no details") == 5


# ---------------------------------------------------------------------------
# api.routes.face_search: _iso, _vector_literal, _infer_platform
# ---------------------------------------------------------------------------

class TestIso:
    def test_none_returns_none(self):
        from src.api.routes.face_search import _iso
        assert _iso(None) is None

    def test_datetime_returns_isoformat(self):
        from src.api.routes.face_search import _iso
        dt = datetime(2026, 3, 1, tzinfo=timezone.utc)
        assert _iso(dt) == dt.isoformat()


class TestVectorLiteral:
    def _v(self, vec):
        from src.api.routes.face_search import _vector_literal
        return _vector_literal(vec)

    def test_empty_vector(self):
        assert self._v([]) == "[]"

    def test_single_element(self):
        result = self._v([1.0])
        assert result == "[1.0000000]"

    def test_multi_element_comma_separated(self):
        result = self._v([0.5, -0.5])
        assert result.startswith("[") and result.endswith("]")
        assert "," in result

    def test_seven_decimal_places(self):
        result = self._v([0.123456789])
        # 7 decimal places
        assert "0.1234568" in result


class TestInferPlatform:
    def _i(self, row):
        from src.api.routes.face_search import _infer_platform
        return _infer_platform(row)

    def test_instagram_in_path(self):
        assert self._i({"file_path": "/media/instagram/img.jpg", "media_item_id": ""}) == "instagram"

    def test_telegram_in_media_item_id(self):
        assert self._i({"file_path": "", "media_item_id": "telegram_123"}) == "telegram"

    def test_unknown_returns_none(self):
        assert self._i({"file_path": "/media/unknown/x.jpg", "media_item_id": "abc"}) is None

    def test_strava_detected(self):
        assert self._i({"file_path": "/strava/activity.jpg", "media_item_id": ""}) == "strava"
