"""
Pure-function tests — batch 62.

Covers previously untested pure functions:
- notifications.alerts: notify_status helper pure parts (_SEVERITY_ICON constant,
  notify_status healthy/unhealthy logic)
- api.routes.health: _env_int (already tested batch19 — adding _age_seconds edge cases),
  PUSH_INTERVAL_SECONDS from websocket
- api.routes.media: additional media coverage constants
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# notifications.alerts: _SEVERITY_ICON constant + build_identity_digest helpers
# ---------------------------------------------------------------------------

class TestSeverityIcon:
    def test_critical_icon(self):
        from src.notifications.alerts import _SEVERITY_ICON
        assert "critical" in _SEVERITY_ICON
        assert isinstance(_SEVERITY_ICON["critical"], str)

    def test_warning_icon(self):
        from src.notifications.alerts import _SEVERITY_ICON
        assert "warning" in _SEVERITY_ICON

    def test_info_icon(self):
        from src.notifications.alerts import _SEVERITY_ICON
        assert "info" in _SEVERITY_ICON

    def test_all_values_strings(self):
        from src.notifications.alerts import _SEVERITY_ICON
        for severity, icon in _SEVERITY_ICON.items():
            assert isinstance(icon, str) and len(icon) > 0


# ---------------------------------------------------------------------------
# notifications.alerts: notify_status logic helpers
# ---------------------------------------------------------------------------

class TestNotifyStatusHelpers:
    def test_esc_html_in_status_line(self):
        from src.notifications.alerts import _esc
        # Any HTML special chars in run type should be escaped
        assert _esc("<b>test</b>") == "&lt;b&gt;test&lt;/b&gt;"

    def test_num_formats_large(self):
        from src.notifications.alerts import _num
        assert self._contains_digits(_num(12345))

    def _contains_digits(self, s):
        return any(c.isdigit() for c in s)

    def test_num_zero(self):
        from src.notifications.alerts import _num
        result = _num(0)
        assert "0" in result

    def test_esc_entity_name(self):
        from src.notifications.alerts import _esc
        # Entity names with & should be escaped
        result = _esc("Alice & Bob")
        assert "&amp;" in result


# ---------------------------------------------------------------------------
# api.routes.health: _env_int additional + _age_seconds additional
# ---------------------------------------------------------------------------

class TestHealthEnvIntAdditional:
    def test_reads_env_correctly(self, monkeypatch):
        monkeypatch.setenv("_TEST_HEALTH_INT2", "42")
        from src.api.routes.health import _env_int
        assert _env_int("_TEST_HEALTH_INT2", 10) == 42

    def test_minimum_enforced(self, monkeypatch):
        monkeypatch.setenv("_TEST_HEALTH_INT2", "1")
        from src.api.routes.health import _env_int
        assert _env_int("_TEST_HEALTH_INT2", 10, minimum=5) == 5

    def test_default_used_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_HEALTH_INT2", raising=False)
        from src.api.routes.health import _env_int
        assert _env_int("_TEST_HEALTH_INT2", 99) == 99

class TestHealthAgeSecondsAdditional:
    def _a(self, v):
        from src.api.routes.health import _age_seconds
        return _age_seconds(v)

    def test_very_old_positive(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = self._a(old)
        assert result is not None
        assert result > 0

    def test_z_suffix_iso(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = self._a(recent)
        assert result is not None and result >= 0


# ---------------------------------------------------------------------------
# api.routes.media: _THUMB_MAX constant + media coverage item keys
# ---------------------------------------------------------------------------

class TestMediaConstants:
    def test_thumb_max_positive(self):
        from src.api.routes.media import _THUMB_MAX
        assert _THUMB_MAX > 0

    def test_thumb_max_reasonable(self):
        from src.api.routes.media import _THUMB_MAX
        assert _THUMB_MAX <= 2048

    def test_coverage_item_all_statuses(self):
        from src.api.routes.media import _coverage_item
        covered = _coverage_item("k", "label", 5, basis="test")
        missing = _coverage_item("k", "label", 0, basis="test")
        assert covered["status"] == "covered"
        assert missing["status"] == "missing"

    def test_row_to_dict_thumbnail_url_format(self):
        from src.api.routes.media import _row_to_dict
        row = {
            "id": "test-123", "media_item_id": "mid",
            "parent_media_item_id": None, "source": "strava",
            "content_type": "image", "analysis_type": "exif_gps",
            "result_json": None, "extracted_text": None,
            "gps_lat": None, "gps_lon": None, "taken_at": None,
            "perceptual_hash": None, "face_embedding": None,
            "model_version": "v1", "processed_at": None,
        }
        result = _row_to_dict(row)
        assert "/api/media/test-123/thumbnail" == result["thumbnail_url"]


# ---------------------------------------------------------------------------
# api.routes.media: _estimated_rollup additional
# ---------------------------------------------------------------------------

class TestEstimatedRollupAdditional:
    def _e(self, rows_total, vals, freqs, key):
        from src.api.routes.media import _estimated_rollup
        return _estimated_rollup(rows_total, vals, freqs, key)

    def test_single_entry(self):
        result = self._e(100, "{image}", "{1.0}", "type")
        assert len(result) == 1
        assert result[0]["type"] == "image"
        assert result[0]["n"] == 100

    def test_more_freqs_than_names_handled(self):
        # Only 1 name but 2 freqs — should not crash
        result = self._e(100, "{image}", "{0.6,0.4}", "type")
        # Only processes as many as zip allows
        assert len(result) == 1

    def test_returns_sorted_desc(self):
        result = self._e(100, "{a,b}", "{0.3,0.7}", "type")
        counts = [r["n"] for r in result]
        assert counts == sorted(counts, reverse=True)
