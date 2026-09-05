"""
QA-lane tests for pure functions in:
- src/pipeline/identity_truth.py: _row_get, _json_dict, coerce_signal,
  corroborated_auto_truth, build_truth_assertion
- src/pipeline/exposure_indicators.py: _exposure_confidence, _normal_domain,
  _host_from_url, _hash_value
"""
from __future__ import annotations

import pytest

from src.pipeline.identity_truth import (
    HARD_SIGNAL_TYPES,
    SignalEvidence,
    _json_dict,
    _row_get,
    build_truth_assertion,
    coerce_signal,
    corroborated_auto_truth,
)
from src.pipeline.exposure_indicators import (
    _exposure_confidence,
    _hash_value,
    _host_from_url,
    _normal_domain,
)


# ---------------------------------------------------------------------------
# identity_truth._row_get
# ---------------------------------------------------------------------------

class TestIdentityTruthRowGet:
    def test_dict_key_access(self):
        assert _row_get({"k": "v"}, "k") == "v"

    def test_missing_key_returns_default(self):
        assert _row_get({"k": "v"}, "missing", "fallback") == "fallback"

    def test_none_row_returns_default(self):
        assert _row_get(None, "key", "def") == "def"

    def test_subscript_object(self):
        class Obj:
            def __getitem__(self, k):
                return "found" if k == "x" else (_ for _ in ()).throw(KeyError(k))
        assert _row_get(Obj(), "x") == "found"


# ---------------------------------------------------------------------------
# identity_truth._json_dict
# ---------------------------------------------------------------------------

class TestJsonDict:
    def test_dict_passthrough(self):
        d = {"a": 1}
        result = _json_dict(d)
        assert result == d
        assert result is not d  # copy

    def test_valid_json_string(self):
        assert _json_dict('{"x": 2}') == {"x": 2}

    def test_json_array_returns_empty(self):
        assert _json_dict("[1,2]") == {}

    def test_invalid_json_returns_empty(self):
        assert _json_dict("bad{") == {}

    def test_none_returns_empty(self):
        assert _json_dict(None) == {}

    def test_integer_returns_empty(self):
        assert _json_dict(42) == {}


# ---------------------------------------------------------------------------
# identity_truth.coerce_signal
# ---------------------------------------------------------------------------

class TestCoerceSignal:
    def _row(self, **kwargs):
        defaults = {
            "id": "sig-1", "signal_type": "email_match",
            "source_platform": "spiderfoot", "source_table": None,
            "value": "alice@example.com", "confidence": 0.9, "metadata": None,
        }
        defaults.update(kwargs)
        return defaults

    def test_basic_coercion(self):
        sig = coerce_signal(self._row())
        assert sig.signal_type == "email_match"
        assert sig.confidence == 0.9
        assert sig.value == "alice@example.com"

    def test_confidence_clamped_to_0_1(self):
        sig = coerce_signal(self._row(confidence=5.0))
        assert sig.confidence == 1.0

    def test_confidence_zero_when_invalid(self):
        sig = coerce_signal(self._row(confidence="bad"))
        assert sig.confidence == 0.0

    def test_source_platform_lowercased(self):
        sig = coerce_signal(self._row(source_platform="SpiderFoot"))
        assert sig.source_platform == "spiderfoot"

    def test_spiderfoot_family(self):
        sig = coerce_signal(self._row(source_platform="spiderfoot"))
        assert sig.family == "spiderfoot"
        assert sig.is_spiderfoot is True

    def test_hard_signal_type_is_hard(self):
        hard_type = next(iter(HARD_SIGNAL_TYPES))
        sig = coerce_signal(self._row(signal_type=hard_type, source_platform="telegram"))
        assert sig.is_hard is True

    def test_spiderfoot_is_never_hard(self):
        hard_type = next(iter(HARD_SIGNAL_TYPES))
        sig = coerce_signal(self._row(signal_type=hard_type, source_platform="spiderfoot"))
        assert sig.is_hard is False


# ---------------------------------------------------------------------------
# identity_truth.corroborated_auto_truth
# ---------------------------------------------------------------------------

class TestCorroboratedAutoTruth:
    def _spiderfoot_row(self, confidence=0.9):
        return {"id": "sf-1", "signal_type": "osint_match", "source_platform": "spiderfoot",
                "source_table": None, "value": "test@example.com", "confidence": confidence, "metadata": None}

    def _hard_row(self, confidence=0.95):
        hard_type = next(iter(HARD_SIGNAL_TYPES))
        return {"id": "h-1", "signal_type": hard_type, "source_platform": "telegram",
                "source_table": None, "value": "test@example.com", "confidence": confidence, "metadata": None}

    def test_requires_both_spiderfoot_and_hard(self):
        ok, conf, summary = corroborated_auto_truth([self._spiderfoot_row()])
        assert ok is False
        assert "requires_spiderfoot_and_independent_hard_signal" in summary.get("reason", "")

    def test_no_signals_returns_false(self):
        ok, _, _ = corroborated_auto_truth([])
        assert ok is False

    def test_spiderfoot_plus_hard_returns_true(self):
        ok, conf, summary = corroborated_auto_truth([self._spiderfoot_row(), self._hard_row()])
        assert ok is True
        assert conf > 0.0

    def test_confidence_capped_at_0_99(self):
        ok, conf, _ = corroborated_auto_truth(
            [self._spiderfoot_row(1.0), self._hard_row(1.0)]
        )
        assert ok is True
        assert conf <= 0.99


# ---------------------------------------------------------------------------
# identity_truth.build_truth_assertion
# ---------------------------------------------------------------------------

class TestBuildTruthAssertion:
    def _spiderfoot_row(self):
        return {"id": "sf-1", "signal_type": "osint_match", "source_platform": "spiderfoot",
                "source_table": None, "value": "test@example.com", "confidence": 0.9, "metadata": None}

    def _hard_row(self):
        hard_type = next(iter(HARD_SIGNAL_TYPES))
        return {"id": "h-1", "signal_type": hard_type, "source_platform": "telegram",
                "source_table": None, "value": "test@example.com", "confidence": 0.95, "metadata": None}

    def test_returns_none_without_corroboration(self):
        result = build_truth_assertion("eid-1", "test@example.com", [self._spiderfoot_row()])
        assert result is None

    def test_returns_dict_with_corroboration(self):
        result = build_truth_assertion("eid-1", "test@example.com",
                                       [self._spiderfoot_row(), self._hard_row()])
        assert result is not None
        assert result["truth_state"] == "auto_truth"
        assert result["entity_id"] == "eid-1"


# ---------------------------------------------------------------------------
# exposure_indicators._exposure_confidence
# ---------------------------------------------------------------------------

class TestExposureConfidence:
    def test_critical_floor(self):
        assert _exposure_confidence({"severity": "critical", "confidence": 0.0}) >= 0.9

    def test_high_floor(self):
        assert _exposure_confidence({"severity": "high", "confidence": 0.0}) >= 0.8

    def test_medium_floor(self):
        assert _exposure_confidence({"severity": "medium", "confidence": 0.0}) >= 0.7

    def test_low_floor(self):
        assert _exposure_confidence({"severity": "low", "confidence": 0.0}) >= 0.55

    def test_unknown_severity_floor(self):
        assert _exposure_confidence({"severity": "unknown", "confidence": 0.0}) >= 0.5

    def test_raw_confidence_beats_floor(self):
        result = _exposure_confidence({"severity": "low", "confidence": 0.99})
        assert result == 0.99


# ---------------------------------------------------------------------------
# exposure_indicators._normal_domain
# ---------------------------------------------------------------------------

class TestNormalDomain:
    def test_valid_domain(self):
        assert _normal_domain("Example.COM") == "example.com"

    def test_strips_dots(self):
        assert _normal_domain(".example.com.") == "example.com"

    def test_no_dot_returns_none(self):
        assert _normal_domain("localhost") is None

    def test_empty_returns_none(self):
        assert _normal_domain("") is None

    def test_none_returns_none(self):
        assert _normal_domain(None) is None


# ---------------------------------------------------------------------------
# exposure_indicators._host_from_url
# ---------------------------------------------------------------------------

class TestHostFromUrl:
    def test_extracts_host(self):
        result = _host_from_url("https://example.com/path")
        assert result == "example.com"

    def test_none_returns_none(self):
        assert _host_from_url(None) is None

    def test_invalid_url_returns_none(self):
        result = _host_from_url("not-a-url")
        assert result is None


# ---------------------------------------------------------------------------
# exposure_indicators._hash_value
# ---------------------------------------------------------------------------

class TestHashValue:
    def test_none_returns_none(self):
        assert _hash_value(None) is None

    def test_returns_16_char_hex(self):
        result = _hash_value("https://example.com")
        assert result is not None
        assert len(result) == 16

    def test_deterministic(self):
        assert _hash_value("test") == _hash_value("test")

    def test_different_inputs_differ(self):
        assert _hash_value("a") != _hash_value("b")
