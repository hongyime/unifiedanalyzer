"""
Pure-function tests — batch 88.

Covers:
- pipeline.exposure_indicators: _row_get, _exposure_confidence,
  _normal_domain, _host_from_url, _hash_value, _redacted_evidence_ref
- pipeline.identity_truth: _row_get, _json_dict, coerce_signal
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# pipeline.exposure_indicators: _row_get
# ---------------------------------------------------------------------------

class TestExposureRowGet:
    def _g(self, row, key, default=None):
        from src.pipeline.exposure_indicators import _row_get
        return _row_get(row, key, default)

    def test_dict_key_found(self):
        assert self._g({"k": 42}, "k") == 42

    def test_dict_key_missing_returns_default(self):
        assert self._g({}, "k", "fallback") == "fallback"

    def test_none_row_returns_default(self):
        assert self._g(None, "k", 0) == 0

    def test_non_dict_subscriptable(self):
        assert self._g(["a", "b"], 1) == "b"


# ---------------------------------------------------------------------------
# pipeline.exposure_indicators: _exposure_confidence
# ---------------------------------------------------------------------------

class TestExposureConfidence:
    def _e(self, row):
        from src.pipeline.exposure_indicators import _exposure_confidence
        return _exposure_confidence(row)

    def test_critical_severity_floor(self):
        result = self._e({"severity": "critical", "confidence": None})
        assert result >= 0.9

    def test_high_severity_floor(self):
        result = self._e({"severity": "high", "confidence": None})
        assert result >= 0.8

    def test_medium_severity_floor(self):
        result = self._e({"severity": "medium", "confidence": None})
        assert result >= 0.7

    def test_unknown_severity_default_floor(self):
        result = self._e({"severity": "unknown", "confidence": None})
        assert result == 0.5

    def test_raw_confidence_used_when_higher(self):
        result = self._e({"severity": "low", "confidence": 0.95})
        assert result == 0.95

    def test_severity_floor_wins_when_higher(self):
        result = self._e({"severity": "critical", "confidence": 0.3})
        assert result >= 0.9


# ---------------------------------------------------------------------------
# pipeline.exposure_indicators: _normal_domain
# ---------------------------------------------------------------------------

class TestNormalDomain:
    def _n(self, v):
        from src.pipeline.exposure_indicators import _normal_domain
        return _normal_domain(v)

    def test_none_returns_none(self):
        assert self._n(None) is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_no_dot_returns_none(self):
        assert self._n("localhost") is None

    def test_valid_domain(self):
        assert self._n("Example.COM") == "example.com"

    def test_strips_leading_trailing_dots(self):
        result = self._n(".example.com.")
        assert result == "example.com"


# ---------------------------------------------------------------------------
# pipeline.exposure_indicators: _host_from_url
# ---------------------------------------------------------------------------

class TestHostFromUrl:
    def _h(self, v):
        from src.pipeline.exposure_indicators import _host_from_url
        return _host_from_url(v)

    def test_none_returns_none(self):
        assert self._h(None) is None

    def test_valid_url_returns_host(self):
        result = self._h("https://www.example.com/path")
        assert result == "www.example.com"

    def test_invalid_url_returns_none(self):
        # No dot in hostname
        result = self._h("http://localhost/path")
        assert result is None

    def test_http_url(self):
        result = self._h("http://example.org")
        assert result == "example.org"


# ---------------------------------------------------------------------------
# pipeline.exposure_indicators: _hash_value
# ---------------------------------------------------------------------------

class TestExposureHashValue:
    def _h(self, v):
        from src.pipeline.exposure_indicators import _hash_value
        return _hash_value(v)

    def test_none_returns_none(self):
        assert self._h(None) is None

    def test_returns_16_char_hex(self):
        result = self._h("test@example.com")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._h("foo") == self._h("foo")


# ---------------------------------------------------------------------------
# pipeline.exposure_indicators: _redacted_evidence_ref
# ---------------------------------------------------------------------------

class TestRedactedEvidenceRef:
    def _r(self, row):
        from src.pipeline.exposure_indicators import _redacted_evidence_ref
        return _redacted_evidence_ref(row)

    def _make(self, **kw):
        defaults = {"id": "r1", "category": "breach", "severity": "high",
                    "domain": "example.com", "url": None, "url_hash": None}
        defaults.update(kw)
        return defaults

    def test_all_keys_present(self):
        result = self._r(self._make())
        for k in ("table", "id", "category", "severity", "domain_hash", "url_hash"):
            assert k in result

    def test_table_is_correct(self):
        result = self._r(self._make())
        assert result["table"] == "collector.exposure_findings"

    def test_domain_is_hashed(self):
        result = self._r(self._make(domain="example.com"))
        assert result["domain_hash"] is not None
        assert len(result["domain_hash"]) == 16


# ---------------------------------------------------------------------------
# pipeline.identity_truth: _row_get
# ---------------------------------------------------------------------------

class TestTruthRowGet:
    def _g(self, row, key, default=None):
        from src.pipeline.identity_truth import _row_get
        return _row_get(row, key, default)

    def test_dict_found(self):
        assert self._g({"k": 5}, "k") == 5

    def test_dict_missing_returns_default(self):
        assert self._g({}, "k", 99) == 99

    def test_none_row_returns_default(self):
        assert self._g(None, "k", "fb") == "fb"


# ---------------------------------------------------------------------------
# pipeline.identity_truth: _json_dict
# ---------------------------------------------------------------------------

class TestTruthJsonDict:
    def _j(self, v):
        from src.pipeline.identity_truth import _json_dict
        return _json_dict(v)

    def test_dict_returned_as_dict(self):
        d = {"k": 1}
        assert self._j(d) == {"k": 1}

    def test_json_string_parsed(self):
        import json
        assert self._j(json.dumps({"x": 5})) == {"x": 5}

    def test_invalid_string_returns_empty(self):
        assert self._j("not json") == {}

    def test_none_returns_empty(self):
        assert self._j(None) == {}

    def test_list_returns_empty(self):
        assert self._j([1, 2]) == {}


# ---------------------------------------------------------------------------
# pipeline.identity_truth: coerce_signal
# ---------------------------------------------------------------------------

class TestCoerceSignal:
    def _make(self, **kw):
        defaults = {
            "id": "sig-1",
            "signal_type": "Email_Match",
            "source_platform": "Telegram",
            "source_table": "telegram_users",
            "value": "alice@example.com",
            "confidence": 0.9,
            "metadata": None,
        }
        defaults.update(kw)
        return defaults

    def _c(self, **kw):
        from src.pipeline.identity_truth import coerce_signal
        return coerce_signal(self._make(**kw))

    def test_signal_type_lowercased(self):
        sig = self._c(signal_type="Email_Match")
        assert sig.signal_type == "email_match"

    def test_source_platform_lowercased(self):
        sig = self._c(source_platform="Telegram")
        assert sig.source_platform == "telegram"

    def test_confidence_clamped_to_one(self):
        sig = self._c(confidence=5.0)
        assert sig.confidence == 1.0

    def test_confidence_clamped_to_zero(self):
        sig = self._c(confidence=-1.0)
        assert sig.confidence == 0.0

    def test_metadata_empty_when_none(self):
        sig = self._c(metadata=None)
        assert isinstance(sig.metadata, dict)

    def test_id_none_when_empty(self):
        sig = self._c(id="")
        assert sig.id is None
