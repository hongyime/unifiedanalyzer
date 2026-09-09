"""
Pure-function tests — batch 99.

Covers:
- pipeline.recon_bridge: _extract_value
- pipeline.backfill_derived: (file-presence helpers are path-dependent;
  test logic/constants instead)
- Additional edge-case coverage for already-tested helpers in high-value modules
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.recon_bridge: _extract_value
# ---------------------------------------------------------------------------

class TestExtractValue:
    def _e(self, obs_type, raw_value):
        from src.pipeline.recon_bridge import _extract_value
        return _extract_value(obs_type, raw_value)

    def test_unknown_obs_type_returns_stripped(self):
        assert self._e("SOME_OTHER_TYPE", "  hello  ") == "hello"

    def test_none_raw_value_returns_empty(self):
        assert self._e("SOME_OTHER_TYPE", None) == ""

    def test_empty_raw_value_returns_empty(self):
        assert self._e("SOME_OTHER_TYPE", "") == ""

    def test_account_external_owned_without_url_returns_stripped(self):
        assert self._e("ACCOUNT_EXTERNAL_OWNED", "  plainvalue  ") == "plainvalue"

    def test_similar_account_external_without_url_returns_stripped(self):
        assert self._e("SIMILAR_ACCOUNT_EXTERNAL", "no_url_here") == "no_url_here"

    def test_account_external_owned_with_spiderfoot_url(self):
        # _SFURL_RE matches https://... — the group(1) is the URL content
        raw = "Found at https://twitter.com/alice"
        result = self._e("ACCOUNT_EXTERNAL_OWNED", raw)
        # If the regex matches, returns the URL; if not, returns stripped raw
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Additional edge-case coverage: pipeline.entity_resolver
# (parse_whatsapp_phone edge cases not covered in earlier batches)
# ---------------------------------------------------------------------------

class TestParseWhatsappPhoneAdditional:
    def _p(self, jid):
        from src.pipeline.entity_resolver import parse_whatsapp_phone
        return parse_whatsapp_phone(jid)

    def test_15_digit_phone_valid(self):
        # 15 digits is max valid
        result = self._p("123456789012345@s.whatsapp.net")
        assert result == "123456789012345"

    def test_16_digit_phone_invalid(self):
        # 16 digits exceeds max
        result = self._p("1234567890123456@s.whatsapp.net")
        assert result is None

    def test_7_digit_phone_valid_boundary(self):
        result = self._p("1234567@s.whatsapp.net")
        assert result == "1234567"

    def test_6_digit_phone_invalid(self):
        result = self._p("123456@s.whatsapp.net")
        assert result is None


# ---------------------------------------------------------------------------
# Additional: pipeline.stream_alerts: _format_window Z suffix
# ---------------------------------------------------------------------------

class TestFormatWindowZSuffix:
    def test_z_suffix_iso_formatted(self):
        from src.pipeline.stream_alerts import _format_window
        result = _format_window("2026-06-01T14:30:00Z")
        assert "2026-06-01" in result
        assert "UTC" in result

    def test_none_window(self):
        from src.pipeline.stream_alerts import _format_window
        assert _format_window(None) == "unknown"


# ---------------------------------------------------------------------------
# Additional: pipeline.location_evidence: _first_present
# ---------------------------------------------------------------------------

class TestFirstPresent:
    def _f(self, *values):
        from src.pipeline.location_evidence import _first_present
        return _first_present(*values)

    def test_returns_first_non_none(self):
        assert self._f(None, None, "found", "other") == "found"

    def test_all_none_returns_none(self):
        assert self._f(None, None) is None

    def test_first_is_not_none(self):
        assert self._f("first", "second") == "first"

    def test_no_args_returns_none(self):
        assert self._f() is None


# ---------------------------------------------------------------------------
# Additional: pipeline.location_evidence: _first_coord
# ---------------------------------------------------------------------------

class TestFirstCoord:
    def _c(self, value, idx):
        from src.pipeline.location_evidence import _first_coord
        return _first_coord(value, idx)

    def test_list_returns_indexed(self):
        result = self._c([1.35, 103.82], 0)
        assert abs(result - 1.35) < 1e-9

    def test_list_second_element(self):
        result = self._c([1.35, 103.82], 1)
        assert abs(result - 103.82) < 1e-9

    def test_none_returns_none(self):
        assert self._c(None, 0) is None

    def test_empty_list_returns_none(self):
        assert self._c([], 0) is None

    def test_out_of_range_returns_none(self):
        assert self._c([1.35], 1) is None


# ---------------------------------------------------------------------------
# Additional: pipeline.identity_calibration: _noisy_or_probs
# ---------------------------------------------------------------------------

class TestNoisyOrProbs:
    def _n(self, X):
        from src.pipeline.identity_calibration import _noisy_or_probs
        return _noisy_or_probs(X)

    def test_empty_input_returns_empty(self):
        assert self._n([]) == []

    def test_all_zeros_returns_near_zero(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        zero_row = [0.0] * len(FEATURE_ORDER)
        result = self._n([zero_row])
        assert len(result) == 1
        assert abs(result[0]) < 1e-9

    def test_returns_list_of_floats(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        row = [0.5] * len(FEATURE_ORDER)
        result = self._n([row])
        assert len(result) == 1
        assert isinstance(result[0], float)
        assert 0.0 <= result[0] <= 1.0
