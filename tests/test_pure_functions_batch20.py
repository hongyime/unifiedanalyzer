"""
Pure-function tests — batch 20.

Covers previously untested modules with no DB or I/O:
- api.routes.search: _semantic_model_ready (env-gated, returns bool)
- api.routes.timeline: _coerce_confidence, _metadata_path_value,
  _derive_timeline_confidence
- api.routes.media: _thumbnail_placeholder (returns SVG), _parse_pg_array_text,
  _estimated_rollup
- db.connection: _parse_dsn, RETRY_DELAYS constant
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# search: _semantic_model_ready
# ---------------------------------------------------------------------------

class TestSemanticModelReady:
    def _r(self):
        from src.api.routes.search import _semantic_model_ready
        return _semantic_model_ready()

    def test_returns_bool(self):
        result = self._r()
        assert isinstance(result, bool)

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("TEXT_SEARCH_HYBRID_SEMANTIC", raising=False)
        from src.api.routes.search import _semantic_model_ready
        assert _semantic_model_ready() is False

    def test_disabled_when_0(self, monkeypatch):
        monkeypatch.setenv("TEXT_SEARCH_HYBRID_SEMANTIC", "0")
        from src.api.routes.search import _semantic_model_ready
        assert _semantic_model_ready() is False


# ---------------------------------------------------------------------------
# timeline: _coerce_confidence, _metadata_path_value, _derive_timeline_confidence
# ---------------------------------------------------------------------------

class TestTimelineCoerceConfidence:
    def _c(self, v):
        from src.api.routes.timeline import _coerce_confidence
        return _coerce_confidence(v)

    def test_float_passthrough(self):
        assert abs(self._c(0.75) - 0.75) < 1e-9

    def test_percentage_string_divided(self):
        assert abs(self._c("75%") - 0.75) < 1e-9

    def test_string_float(self):
        assert abs(self._c("0.9") - 0.9) < 1e-9

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_bool_returns_none(self):
        assert self._c(True) is None

    def test_negative_returns_none(self):
        assert self._c(-0.1) is None

    def test_inf_returns_none(self):
        import math
        assert self._c(math.inf) is None

    def test_over_100_percentage_capped(self):
        # 150 → 1.5 → > 1 → / 100 = 0.015 → capped at 1.0? No: 1.5/100=0.015
        # Actually: raw=150 → > 1 → 150/100 = 1.5 → min(1.5, 1.0) = 1.0
        assert self._c(150) == 1.0

    def test_integer_zero_to_one(self):
        # int 1 → float 1.0, not treated as percentage since ≤ 1
        assert abs(self._c(1) - 1.0) < 1e-9

    def test_non_numeric_string_returns_none(self):
        assert self._c("high") is None


class TestMetadataPathValue:
    def _m(self, metadata, path):
        from src.api.routes.timeline import _metadata_path_value
        return _metadata_path_value(metadata, path)

    def test_single_key(self):
        assert self._m({"confidence": 0.9}, ("confidence",)) == 0.9

    def test_nested_path(self):
        assert self._m({"evidence": {"confidence": 0.7}}, ("evidence", "confidence")) == 0.7

    def test_missing_key_returns_none(self):
        assert self._m({"a": 1}, ("b",)) is None

    def test_non_mapping_returns_none(self):
        assert self._m("not-a-dict", ("k",)) is None

    def test_none_metadata_returns_none(self):
        assert self._m(None, ("k",)) is None

    def test_empty_path_returns_metadata(self):
        d = {"k": 1}
        assert self._m(d, ()) == d


class TestDeriveTimelineConfidence:
    def _d(self, metadata):
        from src.api.routes.timeline import _derive_timeline_confidence
        return _derive_timeline_confidence(metadata)

    def test_top_level_confidence(self):
        conf, source = self._d({"confidence": 0.85})
        assert conf is not None
        assert abs(conf - 0.85) < 1e-9

    def test_nested_evidence_confidence(self):
        conf, source = self._d({"evidence": {"confidence": 0.7}})
        assert conf is not None
        assert "evidence" in source

    def test_no_confidence_returns_none(self):
        conf, source = self._d({"unrelated": "data"})
        assert conf is None
        assert source is None

    def test_none_returns_none(self):
        conf, source = self._d(None)
        assert conf is None

    def test_confidence_rounded_to_4_places(self):
        conf, _ = self._d({"confidence": 0.123456789})
        assert conf == round(0.123456789, 4)


# ---------------------------------------------------------------------------
# media: _thumbnail_placeholder, _parse_pg_array_text, _estimated_rollup
# ---------------------------------------------------------------------------

class TestThumbnailPlaceholder:
    def _t(self, label, detail=""):
        from src.api.routes.media import _thumbnail_placeholder
        return _thumbnail_placeholder(label, detail)

    def test_returns_svg_response(self):
        from fastapi.responses import Response
        result = self._t("VIDEO")
        assert isinstance(result, Response)

    def test_media_type_svg(self):
        result = self._t("PDF")
        assert "svg" in result.media_type

    def test_html_escaping(self):
        result = self._t("<script>")
        assert "<script>" not in result.body.decode()

    def test_label_in_body(self):
        result = self._t("OCR")
        assert b"OCR" in result.body

    def test_cache_control_header(self):
        result = self._t("X")
        assert "Cache-Control" in result.headers


class TestParsePgArrayText:
    def _p(self, raw):
        from src.api.routes.media import _parse_pg_array_text
        return _parse_pg_array_text(raw)

    def test_simple_array(self):
        assert self._p("{image,video,pdf}") == ["image", "video", "pdf"]

    def test_empty_array(self):
        assert self._p("{}") == []

    def test_none_returns_empty(self):
        assert self._p(None) == []

    def test_quoted_elements(self):
        result = self._p('{"image/jpeg","video/mp4"}')
        assert "image/jpeg" in result
        assert "video/mp4" in result

    def test_single_element(self):
        assert self._p("{image}") == ["image"]


class TestEstimatedRollup:
    def _e(self, rows_total, vals, freqs, key):
        from src.api.routes.media import _estimated_rollup
        return _estimated_rollup(rows_total, vals, freqs, key)

    def test_basic_rollup(self):
        result = self._e(100, "{image,video}", "{0.6,0.4}", "type")
        assert len(result) == 2
        types = {r["type"] for r in result}
        assert "image" in types
        assert "video" in types

    def test_estimated_counts_sum_to_total(self):
        result = self._e(100, "{a,b}", "{0.5,0.5}", "k")
        total = sum(r["n"] for r in result)
        assert abs(total - 100) <= 1  # rounding may cause off-by-one

    def test_empty_vals_returns_empty(self):
        assert self._e(100, None, None, "k") == []

    def test_empty_array_returns_empty(self):
        assert self._e(100, "{}", "{}", "k") == []

    def test_invalid_freq_treated_as_zero(self):
        result = self._e(100, "{image}", "{bad}", "type")
        assert result[0]["n"] == 0


# ---------------------------------------------------------------------------
# db.connection: _parse_dsn, RETRY_DELAYS constant
# ---------------------------------------------------------------------------

class TestParseDsn:
    def _p(self, dsn):
        from src.db.connection import _parse_dsn
        return _parse_dsn(dsn)

    def test_standard_url(self):
        result = self._p("postgres://user:pass@myhost:5432/mydb")
        assert result["host"] == "myhost"
        assert result["port"] == 5432
        assert result["user"] == "user"
        assert result["password"] == "pass"
        assert result["database"] == "mydb"

    def test_localhost_converted_to_127(self):
        result = self._p("postgres://user:pass@localhost:5432/db")
        assert result["host"] == "127.0.0.1"

    def test_default_port(self):
        result = self._p("postgres://user:pass@myhost/db")
        assert result["port"] == 5432

    def test_database_name_stripped(self):
        result = self._p("postgres://u:p@h:5432/mydb")
        assert result["database"] == "mydb"

    def test_ip_address_preserved(self):
        result = self._p("postgres://u:p@192.168.1.1:5432/db")
        assert result["host"] == "192.168.1.1"


class TestRetryDelays:
    def test_non_empty(self):
        from src.db.connection import RETRY_DELAYS
        assert len(RETRY_DELAYS) > 0

    def test_increasing(self):
        from src.db.connection import RETRY_DELAYS
        for i in range(len(RETRY_DELAYS) - 1):
            assert RETRY_DELAYS[i] <= RETRY_DELAYS[i + 1]

    def test_all_positive(self):
        from src.db.connection import RETRY_DELAYS
        assert all(d > 0 for d in RETRY_DELAYS)
