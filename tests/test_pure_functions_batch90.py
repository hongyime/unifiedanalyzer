"""
Pure-function tests — batch 90.

Covers:
- pipeline.run_reporting: production_run_types, probe_phase_names,
  is_production_run_type, is_probe_phase_name
- pipeline.face_pair_signals: _pair_key
- pipeline.route_similarity: _cluster
- pipeline.strava_patterns: _decode_meta, _is_default_name
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.run_reporting
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
        assert len(result) >= 1

    def test_is_production_run_type_incremental(self):
        from src.pipeline.run_reporting import is_production_run_type
        assert is_production_run_type("incremental") is True

    def test_is_production_run_type_full_resolution(self):
        from src.pipeline.run_reporting import is_production_run_type
        assert is_production_run_type("full_resolution") is True

    def test_is_production_run_type_unknown(self):
        from src.pipeline.run_reporting import is_production_run_type
        assert is_production_run_type("unknown_type") is False

    def test_is_production_run_type_none(self):
        from src.pipeline.run_reporting import is_production_run_type
        assert is_production_run_type(None) is False

    def test_is_probe_phase_name_forced_failure(self):
        from src.pipeline.run_reporting import is_probe_phase_name
        assert is_probe_phase_name("forced_failure") is True

    def test_is_probe_phase_name_unknown(self):
        from src.pipeline.run_reporting import is_probe_phase_name
        assert is_probe_phase_name("real_phase") is False

    def test_is_probe_phase_name_none(self):
        from src.pipeline.run_reporting import is_probe_phase_name
        assert is_probe_phase_name(None) is False


# ---------------------------------------------------------------------------
# pipeline.face_pair_signals: _pair_key
# ---------------------------------------------------------------------------

class TestFacePairKey:
    def _k(self, a, b):
        from src.pipeline.face_pair_signals import _pair_key
        return _pair_key(a, b)

    def test_order_independent(self):
        assert self._k("a", "b") == self._k("b", "a")

    def test_smaller_first(self):
        result = self._k("z", "a")
        assert result == ("a", "z")

    def test_same_values(self):
        assert self._k("x", "x") == ("x", "x")


# ---------------------------------------------------------------------------
# pipeline.route_similarity: _cluster
# ---------------------------------------------------------------------------

class TestCluster:
    def _c(self, latlng):
        from src.pipeline.route_similarity import _cluster
        return _cluster(latlng)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_empty_returns_none(self):
        assert self._c("") is None

    def test_valid_latlng_string(self):
        result = self._c("1.35,103.82")
        assert result is not None
        assert isinstance(result[0], float)
        assert isinstance(result[1], float)

    def test_invalid_string_returns_none(self):
        assert self._c("notacoord") is None

    def test_precision_applied(self):
        result = self._c("1.3456789012,103.8234567890")
        assert result is not None
        # Result should be rounded
        assert len(str(result[0]).split(".")[-1]) <= 4


# ---------------------------------------------------------------------------
# pipeline.strava_patterns: _decode_meta
# ---------------------------------------------------------------------------

class TestStravaDecodeMeta:
    def _d(self, raw):
        from src.pipeline.strava_patterns import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._d(d) is d

    def test_json_string_parsed(self):
        import json
        assert self._d(json.dumps({"k": 1})) == {"k": 1}

    def test_invalid_returns_empty(self):
        assert self._d("not json") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


# ---------------------------------------------------------------------------
# pipeline.strava_patterns: _is_default_name
# ---------------------------------------------------------------------------

class TestIsDefaultName:
    def _n(self, name):
        from src.pipeline.strava_patterns import _is_default_name
        return _is_default_name(name)

    def test_morning_run_is_default(self):
        assert self._n("Morning Run") is True

    def test_afternoon_ride_is_default(self):
        assert self._n("Afternoon Ride") is True

    def test_custom_name_not_default(self):
        assert self._n("My Epic Trail Run") is False

    def test_case_insensitive(self):
        assert self._n("EVENING WALK") is True

    def test_empty_not_default(self):
        assert self._n("") is False
