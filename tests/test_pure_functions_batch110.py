"""
Pure-function tests — batch 110.

Covers:
- pipeline.recovery_drill: _database_url_for_database, report_to_json
- pipeline.stream_alerts: _hour_bucket additional
- pipeline.data_quality_ledger: _configured_sources
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# pipeline.recovery_drill: _database_url_for_database
# ---------------------------------------------------------------------------

class TestDatabaseUrlForDatabase:
    def _d(self, database_url, database):
        from src.pipeline.recovery_drill import _database_url_for_database
        return _database_url_for_database(database_url, database)

    def test_replaces_db_path(self):
        result = self._d(
            "postgres://user:pass@localhost:5432/unifiedanalyzer",
            "ua_restore_drill_20260601_120000"
        )
        assert "/ua_restore_drill_20260601_120000" in result
        assert "localhost" in result

    def test_preserves_host_and_port(self):
        result = self._d(
            "postgres://user:pass@db.example.com:5432/olddb",
            "ua_restore_drill_20260601_120000"
        )
        assert "db.example.com" in result
        assert "5432" in result

    def test_invalid_database_name_raises(self):
        from src.pipeline.recovery_drill import RecoveryDrillError
        with pytest.raises(RecoveryDrillError):
            self._d("postgres://localhost/db", "DROP TABLE users")


# ---------------------------------------------------------------------------
# pipeline.recovery_drill: report_to_json
# ---------------------------------------------------------------------------

class TestReportToJson:
    def test_returns_json_string(self):
        import json
        from src.pipeline.recovery_drill import RecoveryDrillReport, report_to_json
        report = RecoveryDrillReport(
            backup_path="/backup/test.dump",
            scratch_database="ua_restore_drill_20260601_120000",
            dry_run=True,
        )
        result = report_to_json(report)
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_contains_backup_path(self):
        import json
        from src.pipeline.recovery_drill import RecoveryDrillReport, report_to_json
        report = RecoveryDrillReport(
            backup_path="/backup/my.dump",
            scratch_database="ua_restore_drill_20260601_120000",
            dry_run=False,
        )
        parsed = json.loads(report_to_json(report))
        assert parsed.get("backup_path") == "/backup/my.dump"

    def test_dry_run_field(self):
        import json
        from src.pipeline.recovery_drill import RecoveryDrillReport, report_to_json
        report = RecoveryDrillReport(
            backup_path="/b.dump",
            scratch_database="ua_restore_drill_20260601_120000",
            dry_run=True,
        )
        parsed = json.loads(report_to_json(report))
        assert parsed.get("dry_run") is True


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: _hour_bucket additional
# ---------------------------------------------------------------------------

class TestHourBucketAdditional:
    def _h(self, dt):
        from src.pipeline.stream_alerts import _hour_bucket
        return _hour_bucket(dt)

    def test_midnight_bucket(self):
        dt = datetime(2026, 6, 1, 0, 45, tzinfo=timezone.utc)
        start, end = self._h(dt)
        assert start.hour == 0
        assert start.minute == 0
        assert start.second == 0

    def test_midnight_end_is_1am(self):
        dt = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        start, end = self._h(dt)
        assert end.hour == 1

    def test_tzaware_input(self):
        from datetime import timedelta
        sgt = timezone(timedelta(hours=8))
        dt = datetime(2026, 6, 1, 20, 30, tzinfo=sgt)
        start, end = self._h(dt)
        # Converted to UTC: 12:30 UTC → bucket 12:00–13:00
        assert start.hour == 12
        assert start.tzinfo is not None


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _configured_sources
# ---------------------------------------------------------------------------

class TestConfiguredSources:
    def _c(self, value=None):
        from src.pipeline.data_quality_ledger import _configured_sources
        key = "ANALYZER_DATA_QUALITY_SOURCES"
        if value is not None:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
        try:
            return _configured_sources()
        finally:
            os.environ.pop(key, None)

    def test_returns_list(self):
        assert isinstance(self._c(), list)

    def test_default_non_empty(self):
        result = self._c()
        assert len(result) > 0

    def test_custom_comma_separated(self):
        result = self._c("instagram,telegram,whatsapp")
        assert set(result) == {"instagram", "telegram", "whatsapp"}

    def test_lowercased(self):
        result = self._c("INSTAGRAM,TELEGRAM")
        assert "instagram" in result
        assert "telegram" in result

    def test_whitespace_stripped(self):
        result = self._c(" instagram , telegram ")
        assert "instagram" in result


import pytest
