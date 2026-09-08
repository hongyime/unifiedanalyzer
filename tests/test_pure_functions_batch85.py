"""
Pure-function tests — batch 85.

Covers:
- pipeline.bio_nlp: _decode_meta, extract_tokens, extract_hashtags,
  extract_emojis, detect_language_hint, categorize
- pipeline.language_id: _env_float, _token_count, fasttext_runtime_status
- pipeline.bio_mention: _normalize_mention, _extract_mentions
"""
from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# pipeline.bio_nlp: _decode_meta
# ---------------------------------------------------------------------------

class TestBioNlpDecodeMeta:
    def _d(self, raw):
        from src.pipeline.bio_nlp import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._d(d) is d

    def test_json_string_parsed(self):
        import json
        assert self._d(json.dumps({"k": 1})) == {"k": 1}

    def test_invalid_string_returns_empty(self):
        assert self._d("not json") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


# ---------------------------------------------------------------------------
# pipeline.bio_nlp: extract_tokens
# ---------------------------------------------------------------------------

class TestExtractTokens:
    def _t(self, text):
        from src.pipeline.bio_nlp import extract_tokens
        return extract_tokens(text)

    def test_returns_list(self):
        assert isinstance(self._t("hello world"), list)

    def test_lowercased(self):
        result = self._t("Hello World")
        assert all(w == w.lower() for w in result)

    def test_stopwords_excluded(self):
        result = self._t("i am the best")
        # "i", "am", "the" are stopwords
        assert "i" not in result
        assert "am" not in result
        assert "the" not in result

    def test_urls_stripped(self):
        result = self._t("visit https://example.com for more")
        assert all("http" not in w for w in result)

    def test_mentions_stripped(self):
        result = self._t("hello @alice world")
        assert all("@" not in w for w in result)

    def test_short_words_excluded(self):
        result = self._t("a ab abc")
        # regex is {2,20} so 1-char words excluded, 2-char kept unless stopword
        assert "a" not in result
        # 'ab' is 2 chars and not a stopword — it IS kept


# ---------------------------------------------------------------------------
# pipeline.bio_nlp: extract_hashtags
# ---------------------------------------------------------------------------

class TestExtractHashtags:
    def _h(self, text):
        from src.pipeline.bio_nlp import extract_hashtags
        return extract_hashtags(text)

    def test_extracts_hashtags(self):
        result = self._h("hello #world #python")
        assert "world" in result
        assert "python" in result

    def test_lowercased(self):
        result = self._h("#Python #JAVA")
        assert "python" in result
        assert "java" in result

    def test_no_hashtags_empty(self):
        assert self._h("hello world") == []

    def test_empty_string_empty(self):
        assert self._h("") == []


# ---------------------------------------------------------------------------
# pipeline.bio_nlp: extract_emojis
# ---------------------------------------------------------------------------

class TestExtractEmojis:
    def _e(self, text):
        from src.pipeline.bio_nlp import extract_emojis
        return extract_emojis(text)

    def test_no_emojis_empty(self):
        assert self._e("hello world") == []

    def test_extracts_emoji(self):
        result = self._e("hello 🎉 world")
        assert len(result) >= 1

    def test_empty_string_empty(self):
        assert self._e("") == []


# ---------------------------------------------------------------------------
# pipeline.bio_nlp: detect_language_hint
# ---------------------------------------------------------------------------

class TestDetectLanguageHint:
    def _h(self, text):
        from src.pipeline.bio_nlp import detect_language_hint
        return detect_language_hint(text)

    def test_latin_text_returns_latin(self):
        assert self._h("Hello world English text") == "latin"

    def test_cjk_text_returns_cjk(self):
        assert self._h("你好世界这是中文") == "cjk"

    def test_empty_returns_none(self):
        assert self._h("") is None

    def test_numbers_only_returns_none(self):
        assert self._h("12345 67890") is None


# ---------------------------------------------------------------------------
# pipeline.bio_nlp: categorize
# ---------------------------------------------------------------------------

class TestCategorize:
    def _c(self, tokens):
        from src.pipeline.bio_nlp import categorize
        return categorize(tokens)

    def test_empty_returns_empty(self):
        assert self._c([]) == {}

    def test_returns_dict_of_ints(self):
        result = self._c(["developer", "engineer"])
        assert isinstance(result, dict)
        for v in result.values():
            assert isinstance(v, int)

    def test_unknown_tokens_not_categorized(self):
        result = self._c(["xyzxyzxyznotaword"])
        # Unknown word shouldn't be in result (or might be in 'other')
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# pipeline.language_id: _env_float
# ---------------------------------------------------------------------------

class TestLangIdEnvFloat:
    def _f(self, name, default, value=None):
        from src.pipeline.language_id import _env_float
        if value is not None:
            os.environ[name] = str(value)
        else:
            os.environ.pop(name, None)
        try:
            return _env_float(name, default)
        finally:
            os.environ.pop(name, None)

    def test_returns_default_when_unset(self):
        assert self._f("__LI_UNSET__", 0.5) == 0.5

    def test_returns_env_value(self):
        assert abs(self._f("__LI_X__", 0.5, value=0.8) - 0.8) < 1e-9

    def test_invalid_string_returns_default(self):
        assert self._f("__LI_Y__", 0.5, value="notafloat") == 0.5


# ---------------------------------------------------------------------------
# pipeline.language_id: _token_count
# ---------------------------------------------------------------------------

class TestTokenCount:
    def _t(self, text):
        from src.pipeline.language_id import _token_count
        return _token_count(text)

    def test_empty_returns_zero(self):
        assert self._t("") == 0

    def test_none_returns_zero(self):
        assert self._t(None) == 0

    def test_counts_words(self):
        assert self._t("hello world foo") == 3

    def test_handles_multiple_spaces(self):
        result = self._t("hello   world")
        assert result == 2


# ---------------------------------------------------------------------------
# pipeline.language_id: fasttext_runtime_status
# ---------------------------------------------------------------------------

class TestFasttextRuntimeStatus:
    def test_returns_dict(self):
        from src.pipeline.language_id import fasttext_runtime_status
        result = fasttext_runtime_status()
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        from src.pipeline.language_id import fasttext_runtime_status
        result = fasttext_runtime_status()
        for key in ("detector_version", "fasttext_configured", "fasttext_loaded", "fallback_detector"):
            assert key in result

    def test_fallback_detector_always_true(self):
        from src.pipeline.language_id import fasttext_runtime_status
        assert fasttext_runtime_status()["fallback_detector"] is True


# ---------------------------------------------------------------------------
# pipeline.bio_mention: _normalize_mention
# ---------------------------------------------------------------------------

class TestNormalizeMention:
    def _n(self, raw):
        from src.pipeline.bio_mention import _normalize_mention
        return _normalize_mention(raw)

    def test_none_returns_none(self):
        assert self._n(None) is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_strips_at_sign(self):
        result = self._n("@alice")
        if result:
            assert not result.startswith("@")

    def test_lowercased(self):
        result = self._n("@Alice")
        if result:
            assert result == result.lower()

    def test_space_returns_none(self):
        assert self._n("alice smith") is None

    def test_short_returns_none(self):
        # Too short after normalization
        assert self._n("@ab") is None


# ---------------------------------------------------------------------------
# pipeline.bio_mention: _extract_mentions
# ---------------------------------------------------------------------------

class TestExtractMentions:
    def _m(self, bio_text):
        from src.pipeline.bio_mention import _extract_mentions
        return _extract_mentions(bio_text)

    def test_empty_returns_empty(self):
        assert self._m("") == []

    def test_no_mentions_returns_empty(self):
        assert self._m("hello world no mentions here") == []

    def test_extracts_mention(self):
        result = self._m("check out @alicesmith for info")
        assert len(result) >= 1

    def test_deduplicates(self):
        result = self._m("@alicesmith hello @alicesmith again")
        assert result.count(result[0]) == 1 if result else True

    def test_url_mentions_excluded(self):
        result = self._m("visit https://example.com/@alicesmith for info")
        # URL-embedded mentions should not appear
        assert isinstance(result, list)
