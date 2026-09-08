"""
Pure-function tests — batch 38.

Covers previously untested pure functions:
- pipeline.incremental_runner: _source_label, _normalize_phase_stats,
  _bounded_json_payload, _coverage_payload, _coverage_row (structure only)
- pipeline.media_analysis: _hamming, _PHASH_DISTANCE_THRESHOLD, _PHASH_CONTENT_TYPES
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# incremental_runner: _source_label, _normalize_phase_stats,
#                     _bounded_json_payload, _coverage_payload
# ---------------------------------------------------------------------------

class TestSourceLabel:
    def _s(self, source):
        from src.pipeline.incremental_runner import _source_label
        return _source_label(source)

    def test_plain_string(self):
        assert self._s("instagram") == "instagram"

    def test_none_returns_all(self):
        assert self._s(None) == "all"

    def test_empty_string_returns_all(self):
        assert self._s("") == "all"

    def test_whitespace_returns_all(self):
        assert self._s("   ") == "all"

    def test_truncates_at_128(self):
        long = "a" * 200
        result = self._s(long)
        assert len(result) == 128

    def test_strips_whitespace(self):
        assert self._s("  instagram  ") == "instagram"


class TestNormalizePhaseStats:
    def _n(self, result):
        from src.pipeline.incremental_runner import _normalize_phase_stats
        return _normalize_phase_stats(result)

    def test_dict_passthrough(self):
        assert self._n({"processed": 5}) == {"processed": 5}

    def test_int_wraps_as_attributed(self):
        assert self._n(10) == {"attributed": 10}

    def test_zero_wraps(self):
        assert self._n(0) == {"attributed": 0}

    def test_list_wraps_as_processed(self):
        assert self._n([1, 2, 3]) == {"processed": 3}

    def test_none_returns_empty(self):
        assert self._n(None) == {}

    def test_string_returns_empty(self):
        assert self._n("ok") == {}


class TestBoundedJsonPayload:
    def _b(self, value):
        from src.pipeline.incremental_runner import _bounded_json_payload
        return _bounded_json_payload(value)

    def test_none_returns_empty_list(self):
        assert self._b(None) == []

    def test_empty_string_returns_empty_list(self):
        assert self._b("") == []

    def test_empty_list_returns_empty_list(self):
        assert self._b([]) == []

    def test_empty_dict_returns_empty_list(self):
        assert self._b({}) == []

    def test_list_truncated_at_20(self):
        big = list(range(30))
        result = self._b(big)
        assert len(result) == 20

    def test_dict_truncated_at_20(self):
        big = {str(i): i for i in range(30)}
        result = self._b(big)
        assert len(result) == 20

    def test_short_list_unchanged(self):
        assert self._b([1, 2, 3]) == [1, 2, 3]

    def test_scalar_passthrough(self):
        assert self._b(42) == 42


class TestCoveragePayload:
    def _c(self, stats, status="completed", error=None):
        from src.pipeline.incremental_runner import _coverage_payload
        return _coverage_payload(stats, status, error)

    def test_empty_stats_returns_empty_list(self):
        assert self._c({}) == []

    def test_skipped_key_returned(self):
        result = self._c({"skipped": "drive_unavailable"})
        assert result != []

    def test_error_key_returned(self):
        result = self._c({"error": "timeout"})
        assert result != []

    def test_failed_status_with_error_message(self):
        result = self._c({}, status="failed", error="db_down")
        assert result == {"error": "db_down"}

    def test_failed_status_without_stats_or_error(self):
        result = self._c({}, status="failed")
        assert result == []


# ---------------------------------------------------------------------------
# incremental_runner: _coverage_row structure test
# ---------------------------------------------------------------------------

class TestCoverageRow:
    def test_returns_tuple(self):
        from src.pipeline.incremental_runner import _coverage_row
        row = _coverage_row(
            run_id="run-1",
            run_type="incremental",
            phase="timeline",
            source="all",
            status="completed",
            duration_ms=1234,
            stats={"processed": 100, "attributed": 50},
            error=None,
        )
        assert isinstance(row, tuple)

    def test_contains_run_id(self):
        from src.pipeline.incremental_runner import _coverage_row
        row = _coverage_row(
            run_id="run-42", run_type="incremental", phase="alerts",
            source="all", status="completed", duration_ms=0,
            stats={"new_alerts": 3}, error=None,
        )
        assert "run-42" in row

    def test_failed_status_error_count_at_least_1(self):
        from src.pipeline.incremental_runner import _coverage_row
        row = _coverage_row(
            run_id="r", run_type="incremental", phase="timeline",
            source="all", status="failed", duration_ms=0,
            stats={}, error="db_down",
        )
        # error_count should be >= 1 for failed
        # row is a tuple; error_count is at index 9
        assert row[9] >= 1


# ---------------------------------------------------------------------------
# media_analysis: _hamming, phash constants
# ---------------------------------------------------------------------------

class TestHamming:
    def _h(self, h1, h2):
        from src.pipeline.media_analysis import _hamming
        return _hamming(h1, h2)

    def test_identical_hashes_zero(self):
        assert self._h("ff00ff00", "ff00ff00") == 0

    def test_known_distance(self):
        # ff = 11111111, 00 = 00000000 → 8 bits differ
        assert self._h("ff", "00") == 8

    def test_single_bit_different(self):
        # 01 vs 00 → 1 bit
        assert self._h("01", "00") == 1

    def test_invalid_returns_999(self):
        assert self._h("not-hex", "ff") == 999

    def test_none_returns_999(self):
        assert self._h(None, "ff") == 999

    def test_symmetric(self):
        assert self._h("ab", "cd") == self._h("cd", "ab")


class TestPhashConstants:
    def test_distance_threshold_positive(self):
        from src.pipeline.media_analysis import _PHASH_DISTANCE_THRESHOLD
        assert _PHASH_DISTANCE_THRESHOLD > 0

    def test_distance_threshold_reasonable(self):
        from src.pipeline.media_analysis import _PHASH_DISTANCE_THRESHOLD
        # Should be small (near-duplicate detection, not general similarity)
        assert _PHASH_DISTANCE_THRESHOLD <= 20

    def test_phash_content_types_non_empty(self):
        from src.pipeline.media_analysis import _PHASH_CONTENT_TYPES
        assert len(_PHASH_CONTENT_TYPES) > 0

    def test_phash_image_content_type_present(self):
        from src.pipeline.media_analysis import _PHASH_CONTENT_TYPES
        content_types = [ct for _, ct in _PHASH_CONTENT_TYPES]
        assert "image" in content_types
