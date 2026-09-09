"""
Pure-function tests — batch 101.

Covers:
- pipeline.stream_alerts: format_stream_alert_notification
- pipeline.indicator_export: supabase_export_config, _supabase_mode
"""
from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: format_stream_alert_notification
# ---------------------------------------------------------------------------

class TestFormatStreamAlertNotification:
    def _f(self, rows, dashboard_url="http://localhost:8002"):
        from src.pipeline.stream_alerts import format_stream_alert_notification
        return format_stream_alert_notification(rows, dashboard_url=dashboard_url)

    def _make_row(self, **kw):
        defaults = {
            "alert_type": "silence_gap",
            "source": "instagram",
            "count": 3,
            "window_start": "2026-06-01T00:00:00Z",
            "detail": {},
        }
        defaults.update(kw)
        return defaults

    def test_returns_string(self):
        result = self._f([self._make_row()])
        assert isinstance(result, str)

    def test_contains_dashboard_url(self):
        result = self._f([self._make_row()], dashboard_url="http://localhost:8002")
        assert "localhost:8002" in result

    def test_empty_rows_still_returns_string(self):
        result = self._f([])
        assert "0 grouped alert" in result

    def test_truncates_to_12(self):
        rows = [self._make_row(source=f"src{i}") for i in range(20)]
        result = self._f(rows)
        assert "...and 8 more" in result

    def test_exactly_12_no_truncation(self):
        rows = [self._make_row(source=f"src{i}") for i in range(12)]
        result = self._f(rows)
        assert "...and" not in result

    def test_alert_type_titlecased(self):
        result = self._f([self._make_row(alert_type="silence_gap")])
        assert "Silence Gap" in result

    def test_term_in_detail_shown(self):
        row = self._make_row(detail={"term": "bitcoin"})
        result = self._f([row])
        assert "bitcoin" in result

    def test_count_formatted(self):
        row = self._make_row(count=1234)
        result = self._f([row])
        assert "1,234" in result


# ---------------------------------------------------------------------------
# pipeline.indicator_export: supabase_export_config
# ---------------------------------------------------------------------------

class TestSupabaseExportConfig:
    def _c(self, env=None):
        from src.pipeline.indicator_export import supabase_export_config
        keys = [
            "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY",
            "SUPABASE_DATABASE_URL", "SUPABASE_DB_URL", "SUPABASE_PUBLISHABLE_KEY",
            "SUPABASE_ANON_KEY", "SUPABASE_PROJECT_ID", "SUPABASE_INDICATOR_EXPORT_MODE",
        ]
        for k in keys:
            os.environ.pop(k, None)
        if env:
            for k, v in env.items():
                os.environ[k] = v
        try:
            return supabase_export_config()
        finally:
            for k in keys:
                os.environ.pop(k, None)

    def test_returns_dict(self):
        assert isinstance(self._c(), dict)

    def test_not_configured_when_no_env(self):
        result = self._c()
        assert result["configured"] is False
        assert result["write_method"] == "not_configured"

    def test_postgres_direct_when_db_url(self):
        result = self._c({"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_DATABASE_URL": "postgres://..."})
        assert result["write_method"] == "postgres_direct"
        assert result["configured"] is True

    def test_data_api_when_service_role(self):
        result = self._c({"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "key"})
        assert result["write_method"] == "data_api_secret"

    def test_mode_default_disabled(self):
        assert self._c()["mode"] == "disabled"

    def test_mode_env_override(self):
        result = self._c({"SUPABASE_INDICATOR_EXPORT_MODE": "live"})
        assert result["mode"] == "live"

    def test_payload_field(self):
        assert self._c()["payload"] == "normalized_indicators_only"


# ---------------------------------------------------------------------------
# pipeline.indicator_export: _supabase_mode
# ---------------------------------------------------------------------------

class TestSupabaseMode:
    def _m(self, mode=None, env_val=None):
        from src.pipeline.indicator_export import _supabase_mode
        key = "SUPABASE_INDICATOR_EXPORT_MODE"
        if env_val is not None:
            os.environ[key] = env_val
        else:
            os.environ.pop(key, None)
        try:
            return _supabase_mode(mode)
        finally:
            os.environ.pop(key, None)

    def test_default_disabled(self):
        assert self._m() == "disabled"

    def test_explicit_mode_used(self):
        assert self._m(mode="live") == "live"

    def test_env_mode_used_when_no_explicit(self):
        assert self._m(env_val="dry_run") == "dry_run"

    def test_explicit_overrides_env(self):
        assert self._m(mode="live", env_val="disabled") == "live"

    def test_lowercased_and_stripped(self):
        assert self._m(mode="  LIVE  ") == "live"
