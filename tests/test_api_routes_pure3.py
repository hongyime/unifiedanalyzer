"""
QA-lane tests for pure helper functions in:
- src/api/routes/metrics.py: _line
- src/api/routes/search.py: _semantic_model_ready
- src/api/routes/face_search.py: _iso, _vector_literal
- src/api/routes/intelligence.py: _decode_meta
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# metrics._line
# ---------------------------------------------------------------------------

from src.api.routes.metrics import _line


class TestMetricsLine:
    def test_no_labels(self):
        result = _line("my_gauge", 42)
        assert result == "my_gauge 42"

    def test_with_single_label(self):
        result = _line("requests_total", 100, {"method": "GET"})
        assert 'method="GET"' in result
        assert "requests_total{" in result
        assert "} 100" in result

    def test_with_multiple_labels(self):
        result = _line("errors", 5, {"source": "api", "severity": "high"})
        assert 'source="api"' in result
        assert 'severity="high"' in result

    def test_float_value(self):
        result = _line("ratio", 0.75)
        assert "0.75" in result

    def test_zero_value(self):
        result = _line("counter", 0)
        assert "counter 0" == result

    def test_empty_labels_dict_treated_as_no_labels(self):
        # {} is falsy so falls through to no-label path
        result = _line("gauge", 1, {})
        assert result == "gauge 1"


# ---------------------------------------------------------------------------
# search._semantic_model_ready
# ---------------------------------------------------------------------------

from src.api.routes.search import _semantic_model_ready


class TestSemanticModelReady:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("TEXT_SEARCH_HYBRID_SEMANTIC", raising=False)
        assert _semantic_model_ready() is False

    def test_disabled_when_zero(self, monkeypatch):
        monkeypatch.setenv("TEXT_SEARCH_HYBRID_SEMANTIC", "0")
        assert _semantic_model_ready() is False

    def test_disabled_when_false_string(self, monkeypatch):
        monkeypatch.setenv("TEXT_SEARCH_HYBRID_SEMANTIC", "false")
        assert _semantic_model_ready() is False

    def test_enabled_but_no_model_files(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TEXT_SEARCH_HYBRID_SEMANTIC", "1")
        monkeypatch.setenv("TEXT_EMBED_MODEL_PATH", str(tmp_path / "nonexistent"))
        # Model files don't exist → returns False
        assert _semantic_model_ready() is False

    def test_enabled_with_all_model_files(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TEXT_SEARCH_HYBRID_SEMANTIC", "1")
        monkeypatch.setenv("TEXT_EMBED_MODEL_PATH", str(tmp_path))
        # Create the expected model files
        (tmp_path / "tokenizer.json").write_text("{}")
        (tmp_path / "config.json").write_text("{}")
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_quantized.onnx").write_bytes(b"fake")
        assert _semantic_model_ready() is True


# ---------------------------------------------------------------------------
# face_search._iso, _vector_literal
# ---------------------------------------------------------------------------

from src.api.routes.face_search import _iso, _vector_literal


class TestFaceSearchIso:
    def test_datetime_returns_isoformat(self):
        ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        result = _iso(ts)
        assert "2024-06-01" in result

    def test_none_returns_none(self):
        assert _iso(None) is None

    def test_falsy_zero_returns_none(self):
        assert _iso(0) is None

    def test_non_datetime_with_truthy_value_raises(self):
        # 'if value' is truthy for a string so it tries .isoformat() which doesn't exist.
        # Real callers always pass datetime | None so this is never triggered in prod.
        with pytest.raises(AttributeError):
            _iso("2024-06-01")


class TestVectorLiteral:
    def test_empty_list(self):
        result = _vector_literal([])
        assert result == "[]"

    def test_single_float(self):
        result = _vector_literal([0.5])
        assert result == "[0.5000000]"

    def test_multiple_floats(self):
        result = _vector_literal([0.1, 0.2, 0.3])
        assert result.startswith("[")
        assert result.endswith("]")
        parts = result[1:-1].split(",")
        assert len(parts) == 3

    def test_negative_values(self):
        result = _vector_literal([-0.5, 0.5])
        assert "-0.5000000" in result

    def test_precision_7_decimal_places(self):
        result = _vector_literal([0.123456789])
        # 7 decimal places
        assert "0.1234568" in result or "0.1234567" in result


# ---------------------------------------------------------------------------
# intelligence._decode_meta
# ---------------------------------------------------------------------------

from src.api.routes.intelligence import _decode_meta as intel_decode_meta


class TestIntelligenceDecodeMeta:
    def test_dict_passthrough(self):
        assert intel_decode_meta({"a": 1}) == {"a": 1}

    def test_valid_json_string_parsed(self):
        assert intel_decode_meta('{"x": 2}') == {"x": 2}

    def test_json_array_returns_empty(self):
        assert intel_decode_meta("[1, 2]") == {}

    def test_invalid_json_returns_empty(self):
        assert intel_decode_meta("bad{") == {}

    def test_none_returns_empty(self):
        assert intel_decode_meta(None) == {}

    def test_bytes_parsed(self):
        assert intel_decode_meta(b'{"k":"v"}') == {"k": "v"}

    def test_integer_returns_empty(self):
        assert intel_decode_meta(42) == {}
