"""
Pure-function tests — batch 108.

Covers:
- pipeline.incremental_runner: _coverage_snapshots_for_phase_result
  (result routing + fallback to 'all' source)
- Additional edge-case coverage for high-value already-tested helpers
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.incremental_runner: _coverage_snapshots_for_phase_result
# ---------------------------------------------------------------------------

class TestCoverageSnapshotsForPhaseResult:
    def _c(self, result, phase="resolve_entities", status="ok",
           run_id="run-1", run_type="incremental", duration_ms=1000, error=None):
        from src.pipeline.incremental_runner import _coverage_snapshots_for_phase_result
        return _coverage_snapshots_for_phase_result(
            run_id, run_type, phase, status, duration_ms, result, error
        )

    def test_none_result_returns_all_row(self):
        rows = self._c(None)
        assert len(rows) == 1
        assert rows[0][3] == "all"  # source column

    def test_empty_dict_result_returns_all_row(self):
        rows = self._c({})
        assert len(rows) == 1
        assert rows[0][3] == "all"

    def test_int_result_returns_all_row(self):
        rows = self._c(42)
        assert len(rows) == 1
        assert rows[0][3] == "all"

    def test_source_bucketed_result_returns_per_source(self):
        # Use a known bucket key that's in _COVERAGE_SOURCE_BUCKETS
        from src.pipeline.incremental_runner import _COVERAGE_SOURCE_BUCKETS
        if _COVERAGE_SOURCE_BUCKETS:
            bucket = _COVERAGE_SOURCE_BUCKETS[0]
            result = {
                bucket: {
                    "instagram": {"processed": 10, "attributed": 5},
                    "telegram": {"processed": 20, "attributed": 8},
                }
            }
            rows = self._c(result)
            sources = {r[3] for r in rows}
            assert "instagram" in sources
            assert "telegram" in sources

    def test_returns_list_of_tuples(self):
        rows = self._c({"processed": 5})
        assert isinstance(rows, list)
        for row in rows:
            assert isinstance(row, tuple)

    def test_run_id_and_type_in_row(self):
        rows = self._c({}, run_id="run-xyz", run_type="full_resolution")
        assert rows[0][0] == "run-xyz"
        assert rows[0][1] == "full_resolution"

    def test_phase_in_row(self):
        rows = self._c({}, phase="alerts")
        assert rows[0][2] == "alerts"


# ---------------------------------------------------------------------------
# Additional edge-case coverage: pipeline.incremental_runner
# _coverage_row (test that it returns a tuple of correct arity)
# ---------------------------------------------------------------------------

class TestCoverageRow:
    def _r(self, **kw):
        from src.pipeline.incremental_runner import _coverage_row
        defaults = dict(
            run_id="r1", run_type="incremental", phase="timeline",
            source="instagram", status="ok", duration_ms=500,
            stats={}, error=None,
        )
        defaults.update(kw)
        return _coverage_row(**defaults)

    def test_returns_tuple(self):
        assert isinstance(self._r(), tuple)

    def test_run_id_first_element(self):
        row = self._r(run_id="my-run")
        assert row[0] == "my-run"

    def test_phase_third_element(self):
        row = self._r(phase="alerts")
        assert row[2] == "alerts"

    def test_source_label_truncated(self):
        long_source = "x" * 200
        row = self._r(source=long_source)
        assert len(row[3]) <= 128

    def test_failed_status_increments_error_count(self):
        # error_count is row[9]
        row = self._r(status="failed", error="timeout", stats={})
        assert row[9] >= 1

    def test_skipped_string_increments_skipped_count(self):
        # skipped_count is row[8]
        row = self._r(stats={"skipped": "no_data"}, status="skipped")
        assert row[8] >= 1


# ---------------------------------------------------------------------------
# Additional: pipeline.text_normalizer: _is_emoji
# ---------------------------------------------------------------------------

class TestIsEmoji:
    def _e(self, ch):
        from src.pipeline.text_normalizer import _is_emoji
        return _is_emoji(ch)

    def test_emoji_returns_true(self):
        assert self._e("🎉") is True

    def test_latin_letter_returns_false(self):
        assert self._e("a") is False

    def test_digit_returns_false(self):
        assert self._e("1") is False

    def test_space_returns_false(self):
        assert self._e(" ") is False
