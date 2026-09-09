"""
Pure-function tests — batch 97.

Covers:
- pipeline.media_analysis_tier1: _parse_pgvector
- pipeline.data_quality_ledger: _empty_raw_stage, _empty_analyzer_stage,
  _merge_signal, _source_state, _has_future_timestamp
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# pipeline.media_analysis_tier1: _parse_pgvector
# ---------------------------------------------------------------------------

class TestParsePgvector:
    def _p(self, text):
        from src.pipeline.media_analysis_tier1 import _parse_pgvector
        return _parse_pgvector(text)

    def test_parses_vector_string(self):
        result = self._p("[0.1,0.2,0.3]")
        assert result == [0.1, 0.2, 0.3]

    def test_returns_list(self):
        result = self._p("[1.0,2.0]")
        assert isinstance(result, list)

    def test_empty_vector(self):
        result = self._p("[]")
        assert result == []

    def test_single_element(self):
        result = self._p("[0.5]")
        assert result == [0.5]


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _empty_raw_stage
# ---------------------------------------------------------------------------

class TestEmptyRawStage:
    def _e(self, hours=24):
        from src.pipeline.data_quality_ledger import _empty_raw_stage
        return _empty_raw_stage(hours)

    def test_count_zero(self):
        assert self._e()["count"] == 0

    def test_latest_at_none(self):
        assert self._e()["latest_at"] is None

    def test_lookback_hours_stored(self):
        assert self._e(48)["lookback_hours"] == 48

    def test_signals_empty_list(self):
        assert self._e()["signals"] == []


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _empty_analyzer_stage
# ---------------------------------------------------------------------------

class TestEmptyAnalyzerStage:
    def _e(self, hours=24):
        from src.pipeline.data_quality_ledger import _empty_analyzer_stage
        return _empty_analyzer_stage(hours)

    def test_total_analyzer_signals_zero(self):
        assert self._e()["total_analyzer_signals"] == 0

    def test_lookback_hours_stored(self):
        assert self._e(48)["lookback_hours"] == 48

    def test_timeline_events_present(self):
        assert "timeline_events" in self._e()

    def test_latest_at_none(self):
        assert self._e()["latest_at"] is None


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _merge_signal
# ---------------------------------------------------------------------------

class TestMergeSignal:
    def _m(self, stage, table, count, latest=None):
        from src.pipeline.data_quality_ledger import _merge_signal
        _merge_signal(stage, table, count, latest)
        return stage

    def test_count_incremented(self):
        stage = {"count": 0, "signals": [], "latest_at": None, "latest_age_seconds": None}
        result = self._m(stage, "timeline_events", 5)
        assert result["count"] == 5

    def test_signal_appended(self):
        stage = {"count": 0, "signals": [], "latest_at": None, "latest_age_seconds": None}
        result = self._m(stage, "timeline_events", 3)
        assert len(result["signals"]) == 1
        assert result["signals"][0]["table"] == "timeline_events"

    def test_accumulates_across_calls(self):
        stage = {"count": 0, "signals": [], "latest_at": None, "latest_age_seconds": None}
        self._m(stage, "table_a", 3)
        self._m(stage, "table_b", 7)
        assert stage["count"] == 10
        assert len(stage["signals"]) == 2


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _source_state
# ---------------------------------------------------------------------------

class TestSourceState:
    def _s(self, raw_count=0, analyzer_count=0, indicator_count=0, exported_count=0):
        from src.pipeline.data_quality_ledger import _source_state
        raw = {"count": raw_count, "latest_at": None, "latest_age_seconds": None}
        analyzer = {
            "total_analyzer_signals": analyzer_count,
            "latest_at": None,
            "latest_age_seconds": None,
            "normalized_indicators": {"count": indicator_count},
            "supabase_exported_indicators": {"count": exported_count},
        }
        return _source_state(raw, analyzer)

    def test_both_zero_is_quiet(self):
        state, _ = self._s(0, 0)
        assert state == "quiet"

    def test_raw_only_is_gap(self):
        state, _ = self._s(5, 0)
        assert state == "gap"

    def test_analyzer_only_is_analyzer_only(self):
        state, _ = self._s(0, 5)
        assert state == "analyzer_only"

    def test_both_present_is_ok(self):
        state, _ = self._s(5, 5)
        assert state == "ok"

    def test_indicator_no_export_is_export_gap(self):
        state, _ = self._s(5, 5, indicator_count=3, exported_count=0)
        assert state == "export_gap"

    def test_indicator_with_export_is_ok(self):
        state, _ = self._s(5, 5, indicator_count=3, exported_count=3)
        assert state == "ok"


# ---------------------------------------------------------------------------
# pipeline.data_quality_ledger: _has_future_timestamp
# ---------------------------------------------------------------------------

class TestHasFutureTimestamp:
    def _h(self, value):
        from src.pipeline.data_quality_ledger import _has_future_timestamp
        return _has_future_timestamp(value)

    def test_empty_dict_false(self):
        assert self._h({}) is False

    def test_positive_age_false(self):
        assert self._h({"latest_age_seconds": 100}) is False

    def test_negative_age_true(self):
        assert self._h({"latest_age_seconds": -500}) is True

    def test_nested_negative_age_true(self):
        assert self._h({"signals": [{"latest_age_seconds": -100}]}) is True

    def test_none_age_false(self):
        assert self._h({"latest_age_seconds": None}) is False

    def test_list_with_future_true(self):
        assert self._h([{"latest_age_seconds": -50}]) is True

    def test_list_all_positive_false(self):
        assert self._h([{"latest_age_seconds": 10}, {"latest_age_seconds": 20}]) is False

    def test_non_dict_non_list_false(self):
        assert self._h("not a dict") is False
