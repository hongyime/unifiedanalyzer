"""
Pure-function tests — batch 37.

Covers previously untested pure functions:
- pipeline.incremental_runner: _secondary_phases, _sum_numeric_stats,
  _safe_count, _count_from_keys, _phase_resource_class
- pipeline.media_analysis: _extract_exif_device (pure dict builder)
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# incremental_runner: _secondary_phases, _sum_numeric_stats, _safe_count,
#                     _count_from_keys, _phase_resource_class
# ---------------------------------------------------------------------------

class TestSecondaryPhases:
    def test_returns_list(self):
        from src.pipeline.incremental_runner import _secondary_phases
        result = _secondary_phases()
        assert isinstance(result, list)

    def test_non_empty(self):
        from src.pipeline.incremental_runner import _secondary_phases
        assert len(_secondary_phases()) > 10

    def test_each_element_is_tuple(self):
        from src.pipeline.incremental_runner import _secondary_phases
        for item in _secondary_phases():
            assert isinstance(item, tuple)
            assert len(item) == 2

    def test_known_phases_present(self):
        from src.pipeline.incremental_runner import _secondary_phases
        phase_names = {name for name, _ in _secondary_phases()}
        for name in ("identity_scoring", "face_clustering", "geocode",
                     "location_inference", "bio_nlp"):
            assert name in phase_names, f"{name} missing from secondary phases"

    def test_callables_in_second_element(self):
        from src.pipeline.incremental_runner import _secondary_phases
        for name, fn in _secondary_phases():
            assert callable(fn), f"{name}: fn not callable"


class TestSumNumericStats:
    def _s(self, stats):
        from src.pipeline.incremental_runner import _sum_numeric_stats
        return _sum_numeric_stats(stats)

    def test_sums_integers(self):
        assert self._s({"a": 3, "b": 5}) == 8

    def test_ignores_strings(self):
        assert self._s({"a": 3, "skipped": "yes"}) == 3

    def test_ignores_booleans(self):
        assert self._s({"a": 3, "ok": True}) == 3

    def test_empty_dict(self):
        assert self._s({}) == 0

    def test_floats_summed(self):
        result = self._s({"a": 1.5, "b": 2.5})
        assert int(result) == 4

    def test_none_ignored(self):
        assert self._s({"a": 3, "b": None}) == 3


class TestSafeCount:
    def _c(self, value):
        from src.pipeline.incremental_runner import _safe_count
        return _safe_count(value)

    def test_int_returned(self):
        assert self._c(5) == 5

    def test_float_truncated(self):
        assert self._c(3.9) == 3

    def test_negative_clamped_to_zero(self):
        assert self._c(-5) == 0

    def test_bool_returns_none(self):
        assert self._c(True) is None
        assert self._c(False) is None

    def test_string_returns_none(self):
        assert self._c("5") is None

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_zero(self):
        assert self._c(0) == 0


class TestCountFromKeys:
    def _c(self, stats, keys):
        from src.pipeline.incremental_runner import _count_from_keys
        return _count_from_keys(stats, keys)

    def test_finds_first_matching_key(self):
        assert self._c({"processed": 10, "total": 20}, ("processed", "total")) == 10

    def test_falls_back_to_second(self):
        assert self._c({"total": 20}, ("processed", "total")) == 20

    def test_no_match_returns_zero(self):
        assert self._c({"events": 5}, ("processed", "total")) == 0

    def test_bool_value_skipped(self):
        # bool is ignored by _safe_count → falls through
        assert self._c({"processed": True, "total": 7}, ("processed", "total")) == 7

    def test_empty_stats_returns_zero(self):
        assert self._c({}, ("processed",)) == 0


class TestPhaseResourceClass:
    def _p(self, phase):
        from src.pipeline.incremental_runner import _phase_resource_class
        return _phase_resource_class(phase)

    def test_known_phase_returns_class(self):
        from src.pipeline.incremental_runner import _PHASE_RESOURCE_CLASSES
        for phase, expected in _PHASE_RESOURCE_CLASSES.items():
            assert self._p(phase) == expected

    def test_unknown_phase_defaults_to_db(self):
        assert self._p("unknown_phase_xyz") == "db"

    def test_returns_string(self):
        assert isinstance(self._p("timeline"), str)


# ---------------------------------------------------------------------------
# media_analysis: _extract_exif_device (pure dict builder, no I/O)
# ---------------------------------------------------------------------------

class TestExtractExifDevice:
    def _e(self, exif_dict, exif_ifd_dict):
        from src.pipeline.media_analysis import _extract_exif_device
        return _extract_exif_device(exif_dict, exif_ifd_dict)

    def test_no_serial_returns_none(self):
        # Make+Model without serial → not distinctive
        exif = {271: "Apple", 272: "iPhone 13"}
        result = self._e(exif, {})
        assert result is None

    def test_body_serial_present(self):
        from src.pipeline.media_analysis import _EXIF_BODY_SERIAL, _EXIF_MAKE, _EXIF_MODEL
        exif = {_EXIF_MAKE: "Canon", _EXIF_MODEL: "EOS R5"}
        exif_ifd = {_EXIF_BODY_SERIAL: "083060001234"}
        result = self._e(exif, exif_ifd)
        assert result is not None
        assert result["body_serial"] == "083060001234"
        assert result["make"] == "Canon"
        assert result["model"] == "EOS R5"

    def test_lens_serial_only_sufficient(self):
        from src.pipeline.media_analysis import _EXIF_LENS_SERIAL
        result = self._e({}, {_EXIF_LENS_SERIAL: "LENS-456"})
        assert result is not None
        assert result["lens_serial"] == "LENS-456"

    def test_empty_serial_returns_none(self):
        from src.pipeline.media_analysis import _EXIF_BODY_SERIAL
        result = self._e({}, {_EXIF_BODY_SERIAL: "  "})
        assert result is None

    def test_both_serials_present(self):
        from src.pipeline.media_analysis import _EXIF_BODY_SERIAL, _EXIF_LENS_SERIAL
        result = self._e({}, {_EXIF_BODY_SERIAL: "B123", _EXIF_LENS_SERIAL: "L456"})
        assert result is not None
        assert result["body_serial"] == "B123"
        assert result["lens_serial"] == "L456"

    def test_result_has_all_keys(self):
        from src.pipeline.media_analysis import _EXIF_BODY_SERIAL
        result = self._e({}, {_EXIF_BODY_SERIAL: "X1"})
        assert result is not None
        for key in ("make", "model", "lens_model", "body_serial", "lens_serial"):
            assert key in result
