"""
Pure-function tests — batch 12.

Covers previously untested modules with no DB or I/O:
- pipeline.run_reporting: production_run_types, probe_phase_names,
  is_production_run_type, is_probe_phase_name
- pipeline.data_quality_ledger: _configured_sources, _iso, _max_dt, _age_seconds
- face.discovery.scanner: FileRecord.to_dict
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# run_reporting
# ---------------------------------------------------------------------------

class TestRunReporting:
    def test_production_run_types_returns_list(self):
        from src.pipeline.run_reporting import production_run_types
        result = production_run_types()
        assert isinstance(result, list)
        assert "incremental" in result
        assert "full_resolution" in result

    def test_probe_phase_names_returns_list(self):
        from src.pipeline.run_reporting import probe_phase_names
        result = probe_phase_names()
        assert isinstance(result, list)
        assert "forced_failure" in result

    def test_is_production_run_type_true(self):
        from src.pipeline.run_reporting import is_production_run_type
        assert is_production_run_type("incremental") is True
        assert is_production_run_type("full_resolution") is True

    def test_is_production_run_type_false(self):
        from src.pipeline.run_reporting import is_production_run_type
        assert is_production_run_type("debug") is False
        assert is_production_run_type(None) is False
        assert is_production_run_type("") is False

    def test_is_probe_phase_name_true(self):
        from src.pipeline.run_reporting import is_probe_phase_name
        assert is_probe_phase_name("forced_failure") is True

    def test_is_probe_phase_name_false(self):
        from src.pipeline.run_reporting import is_probe_phase_name
        assert is_probe_phase_name("real_phase") is False
        assert is_probe_phase_name(None) is False

    def test_lists_are_independent_copies(self):
        from src.pipeline.run_reporting import production_run_types
        a = production_run_types()
        b = production_run_types()
        a.append("extra")
        assert "extra" not in b


# ---------------------------------------------------------------------------
# data_quality_ledger: _configured_sources, _iso, _max_dt, _age_seconds
# ---------------------------------------------------------------------------

class TestConfiguredSources:
    def test_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ANALYZER_DATA_QUALITY_SOURCES", raising=False)
        from src.pipeline.data_quality_ledger import _configured_sources, DEFAULT_LEDGER_SOURCES
        result = _configured_sources()
        assert result == list(DEFAULT_LEDGER_SOURCES)

    def test_parses_csv_env(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_SOURCES", "instagram, telegram, github")
        from src.pipeline.data_quality_ledger import _configured_sources
        result = _configured_sources()
        assert result == ["instagram", "telegram", "github"]

    def test_lowercases(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_SOURCES", "Instagram,Telegram")
        from src.pipeline.data_quality_ledger import _configured_sources
        result = _configured_sources()
        assert "instagram" in result
        assert "telegram" in result

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_SOURCES", " instagram , telegram ")
        from src.pipeline.data_quality_ledger import _configured_sources
        result = _configured_sources()
        assert result == ["instagram", "telegram"]


class TestDataQualityIso:
    def _i(self, value):
        from src.pipeline.data_quality_ledger import _iso
        return _iso(value)

    def test_datetime_returns_isoformat(self):
        dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = self._i(dt)
        assert "2026-01-15" in result

    def test_none_returns_none(self):
        assert self._i(None) is None

    def test_string_without_isoformat_returns_none(self):
        assert self._i("not-a-datetime") is None

    def test_int_returns_none(self):
        assert self._i(42) is None


class TestDataQualityMaxDt:
    def _m(self, values):
        from src.pipeline.data_quality_ledger import _max_dt
        return _max_dt(values)

    def test_returns_max(self):
        a = datetime(2026, 1, 1, tzinfo=timezone.utc)
        b = datetime(2026, 6, 1, tzinfo=timezone.utc)
        assert self._m([a, b]) == b

    def test_ignores_none(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert self._m([None, dt, None]) == dt

    def test_all_none_returns_none(self):
        assert self._m([None, None]) is None

    def test_empty_list_returns_none(self):
        assert self._m([]) is None

    def test_single_value(self):
        dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
        assert self._m([dt]) == dt


class TestDataQualityAgeSeconds:
    def _a(self, value):
        from src.pipeline.data_quality_ledger import _age_seconds
        return _age_seconds(value)

    def test_none_returns_none(self):
        assert self._a(None) is None

    def test_recent_datetime_non_negative(self):
        recent = datetime.now(timezone.utc) - timedelta(seconds=10)
        result = self._a(recent)
        assert result is not None
        assert result >= 0

    def test_old_datetime_positive(self):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        result = self._a(old)
        assert result is not None
        assert result >= 3500

    def test_iso_string_parsed(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = self._a(old)
        assert result is not None
        assert result >= 290

    def test_invalid_string_returns_none(self):
        assert self._a("not-a-date") is None

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime.utcnow() - timedelta(seconds=60)
        result = self._a(naive)
        assert result is not None
        assert result >= 0

    def test_z_suffix_iso_string(self):
        old_z = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = self._a(old_z)
        assert result is not None
        assert result >= 100


# ---------------------------------------------------------------------------
# face.discovery.scanner: FileRecord.to_dict
# ---------------------------------------------------------------------------

class TestFileRecord:
    def _make(self, **kw):
        from src.face.discovery.scanner import FileRecord
        defaults = dict(path="/mnt/c/photo.jpg", size=1024, mtime=1700000000.0,
                        extension=".jpg", drive_type="local", source_root="/mnt/c")
        defaults.update(kw)
        return FileRecord(**defaults)

    def test_to_dict_has_all_keys(self):
        record = self._make()
        d = record.to_dict()
        for key in ("path", "size", "mtime", "extension", "drive_type", "source_root"):
            assert key in d

    def test_to_dict_values_match(self):
        record = self._make(path="/img.jpg", size=2048, extension=".jpg")
        d = record.to_dict()
        assert d["path"] == "/img.jpg"
        assert d["size"] == 2048
        assert d["extension"] == ".jpg"

    def test_default_drive_type(self):
        from src.face.discovery.scanner import FileRecord
        record = FileRecord(path="/x.jpg", size=100, mtime=0.0, extension=".jpg")
        assert record.drive_type == "local"

    def test_default_source_root_empty(self):
        from src.face.discovery.scanner import FileRecord
        record = FileRecord(path="/x.jpg", size=100, mtime=0.0, extension=".jpg")
        assert record.source_root == ""

    def test_custom_drive_type(self):
        record = self._make(drive_type="usb")
        assert record.to_dict()["drive_type"] == "usb"
