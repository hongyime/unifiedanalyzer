"""
Pure-function tests — batch 84.

Covers:
- pipeline.sentiment_emotion: _tokens, detect_language, _fallback_vader,
  _score_nrc
- pipeline.cross_source_signals: _is_identity_domain, _domain
- pipeline.graph_overlap: _norm
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.sentiment_emotion: _tokens
# ---------------------------------------------------------------------------

class TestSentimentTokens:
    def _t(self, text):
        from src.pipeline.sentiment_emotion import _tokens
        return _tokens(text)

    def test_empty_string_returns_empty(self):
        assert self._t("") == []

    def test_none_handled(self):
        assert self._t(None) == []

    def test_lowercased(self):
        result = self._t("Hello World")
        assert all(w == w.lower() for w in result)

    def test_punctuation_excluded(self):
        result = self._t("hello, world!")
        assert "," not in result
        assert "!" not in result
        assert "hello" in result

    def test_multiple_words(self):
        result = self._t("one two three")
        assert len(result) == 3


# ---------------------------------------------------------------------------
# pipeline.sentiment_emotion: detect_language
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def _d(self, text):
        from src.pipeline.sentiment_emotion import detect_language
        return detect_language(text)

    def test_empty_returns_und(self):
        lang, conf, meta = self._d("")
        assert lang == "und"

    def test_whitespace_only_returns_und(self):
        lang, conf, meta = self._d("   ")
        assert lang == "und"

    def test_latin_text_returns_en(self):
        lang, conf, meta = self._d("Hello this is an English sentence")
        assert lang == "en"

    def test_confidence_between_0_and_1(self):
        lang, conf, meta = self._d("Hello world")
        assert 0.0 <= conf <= 1.0

    def test_non_latin_returns_unsupported(self):
        # Chinese characters have no latin
        lang, conf, meta = self._d("你好世界")
        assert lang == "unsupported"


# ---------------------------------------------------------------------------
# pipeline.sentiment_emotion: _fallback_vader
# ---------------------------------------------------------------------------

class TestFallbackVader:
    def _f(self, text):
        from src.pipeline.sentiment_emotion import _fallback_vader, _tokens
        words = _tokens(text)
        return _fallback_vader(text, words)

    def test_returns_dict_with_keys(self):
        result = self._f("great wonderful amazing")
        for key in ("compound", "pos", "neu", "neg"):
            assert key in result

    def test_neutral_text_compound_near_zero(self):
        result = self._f("the table is on the floor")
        assert -0.5 < result["compound"] < 0.5

    def test_positive_text_positive_compound(self):
        result = self._f("love great wonderful amazing")
        assert result["compound"] > 0

    def test_neg_not_negative(self):
        result = self._f("great")
        assert result["neg"] >= 0.0

    def test_pos_not_negative(self):
        result = self._f("terrible")
        assert result["pos"] >= 0.0


# ---------------------------------------------------------------------------
# pipeline.sentiment_emotion: _score_nrc
# ---------------------------------------------------------------------------

class TestScoreNrc:
    def _n(self, words):
        from src.pipeline.sentiment_emotion import _score_nrc
        return _score_nrc(words)

    def test_empty_words_returns_empty(self):
        assert self._n([]) == {}

    def test_returns_dict(self):
        result = self._n(["love", "happy"])
        assert isinstance(result, dict)

    def test_unknown_words_returns_empty(self):
        assert self._n(["xyzxyzxyz"]) == {}

    def test_values_between_0_and_1(self):
        result = self._n(["love", "hate", "fear"])
        for v in result.values():
            assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# pipeline.cross_source_signals: _is_identity_domain
# ---------------------------------------------------------------------------

class TestIsIdentityDomain:
    def _i(self, domain):
        from src.pipeline.cross_source_signals import _is_identity_domain
        return _is_identity_domain(domain)

    def test_none_returns_false(self):
        assert self._i(None) is False

    def test_empty_returns_false(self):
        assert self._i("") is False

    def test_unknown_domain_returns_false(self):
        assert self._i("example.com") is False

    def test_known_identity_domain(self):
        # keybase.io, about.me, linktr.ee are common identity domains
        # Check which ones are actually in _IDENTITY_DOMAINS
        from src.pipeline.cross_source_signals import _IDENTITY_DOMAINS
        if _IDENTITY_DOMAINS:
            domain = next(iter(_IDENTITY_DOMAINS))
            assert self._i(domain) is True

    def test_subdomain_of_identity_domain(self):
        from src.pipeline.cross_source_signals import _IDENTITY_DOMAINS
        if _IDENTITY_DOMAINS:
            domain = next(iter(_IDENTITY_DOMAINS))
            assert self._i(f"sub.{domain}") is True


# ---------------------------------------------------------------------------
# pipeline.cross_source_signals: _domain
# ---------------------------------------------------------------------------

class TestCrossSourceDomain:
    def _d(self, url):
        from src.pipeline.cross_source_signals import _domain
        return _domain(url)

    def test_none_returns_none(self):
        assert self._d(None) is None

    def test_empty_returns_none(self):
        assert self._d("") is None

    def test_full_url_returns_domain(self):
        result = self._d("https://www.example.com/path")
        assert result == "example.com"

    def test_no_scheme_returns_domain(self):
        result = self._d("example.com/path")
        assert result == "example.com"

    def test_http_url(self):
        result = self._d("http://example.org")
        assert result == "example.org"

    def test_lowercased(self):
        result = self._d("https://Example.COM")
        assert result == result.lower()


# ---------------------------------------------------------------------------
# pipeline.graph_overlap: _norm
# ---------------------------------------------------------------------------

class TestGraphOverlapNorm:
    def _n(self, s):
        from src.pipeline.graph_overlap import _norm
        return _norm(s)

    def test_none_returns_none(self):
        assert self._n(None) is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_lowercased(self):
        assert self._n("Alice") == "alice"

    def test_strips_whitespace(self):
        assert self._n("  hello  ") == "hello"

    def test_already_clean(self):
        assert self._n("alice") == "alice"
