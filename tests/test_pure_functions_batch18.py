"""
Pure-function tests — batch 18.

Covers previously untested modules with no DB or I/O:
- pipeline.text_normalizer: text_sha1, normalize_social_text, _coerce_json,
  _select_metadata, _domain_from_url, _is_emoji, source_fingerprint,
  _clean, _append_text
- pipeline.translation_worker: normalize_translation_language, opus_model_name,
  nllb_language_code, translation_max_per_run, text_version_hash,
  translation_decision, TranslationDecision, NoopTranslator
- api.routes.graph: _relationship_why
"""
from __future__ import annotations

import hashlib
import pytest


# ---------------------------------------------------------------------------
# text_normalizer: text_sha1, normalize_social_text, _coerce_json,
#                  _select_metadata, _domain_from_url, _is_emoji
# ---------------------------------------------------------------------------

class TestTextSha1:
    def _s(self, text):
        from src.pipeline.text_normalizer import text_sha1
        return text_sha1(text)

    def test_known_value(self):
        expected = hashlib.sha1("hello".encode()).hexdigest()
        assert self._s("hello") == expected

    def test_empty_string(self):
        expected = hashlib.sha1(b"").hexdigest()
        assert self._s("") == expected

    def test_none_treated_as_empty(self):
        assert self._s(None) == self._s("")

    def test_returns_40_hex_chars(self):
        result = self._s("test")
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._s("same") == self._s("same")


class TestNormalizeSocialText:
    def _n(self, text, max_chars=8000):
        from src.pipeline.text_normalizer import normalize_social_text
        return normalize_social_text(text, max_chars=max_chars)

    def test_basic_passthrough(self):
        assert self._n("hello world") == "hello world"

    def test_strips_leading_trailing_whitespace(self):
        assert self._n("  hello  ") == "hello"

    def test_normalizes_multiple_newlines(self):
        result = self._n("a\n\n\n\nb")
        assert "a\n\nb" == result

    def test_normalizes_crlf(self):
        result = self._n("a\r\nb")
        assert "\r" not in result

    def test_truncates_at_max_chars(self):
        long_text = "a" * 100
        result = self._n(long_text, max_chars=50)
        assert len(result) <= 50

    def test_none_returns_empty(self):
        assert self._n(None) == ""

    def test_max_chars_zero_no_truncation(self):
        long_text = "a" * 100
        result = self._n(long_text, max_chars=0)
        assert len(result) == 100


class TestCoerceJson:
    def _c(self, v):
        from src.pipeline.text_normalizer import _coerce_json
        return _coerce_json(v)

    def test_none_returns_empty(self):
        assert self._c(None) == {}

    def test_dict_returns_dict(self):
        assert self._c({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._c('{"a": 1}') == {"a": 1}

    def test_invalid_json_returns_raw(self):
        result = self._c("bad")
        assert "raw" in result

    def test_json_array_returns_raw(self):
        result = self._c("[1, 2]")
        assert "raw" in result


class TestSelectMetadata:
    def _s(self, metadata):
        from src.pipeline.text_normalizer import _select_metadata
        return _select_metadata(metadata)

    def test_known_key_included(self):
        result = self._s({"caption": "hello"})
        assert "caption" in result

    def test_unknown_key_excluded(self):
        result = self._s({"unknown_key": "value"})
        assert "unknown_key" not in result

    def test_none_value_excluded(self):
        result = self._s({"caption": None})
        assert "caption" not in result

    def test_empty_string_excluded(self):
        result = self._s({"caption": ""})
        assert "caption" not in result

    def test_empty_dict_input(self):
        assert self._s({}) == {}


class TestDomainFromUrl:
    def _d(self, url):
        from src.pipeline.text_normalizer import _domain_from_url
        return _domain_from_url(url)

    def test_basic_url(self):
        assert self._d("https://example.com/page") == "example.com"

    def test_strips_www(self):
        assert self._d("https://www.example.com") == "example.com"

    def test_subdomain_preserved(self):
        assert self._d("https://blog.example.com") == "blog.example.com"

    def test_empty_url(self):
        assert self._d("") is None


class TestIsEmoji:
    def _e(self, ch):
        from src.pipeline.text_normalizer import _is_emoji
        return _is_emoji(ch)

    def test_emoji_char(self):
        assert self._e("😀") is True

    def test_regular_char_not_emoji(self):
        assert self._e("a") is False

    def test_symbol_in_range(self):
        # U+2600 = ☀ — in emoji range
        assert self._e("☀") is True


class TestSourceFingerprint:
    def test_deterministic(self):
        from src.pipeline.text_normalizer import source_fingerprint
        row = {"id": "123", "entity_id": "eid", "source": "instagram",
               "event_type": "CONTENT_PUBLISHED", "occurred_at": "2026-01-01",
               "source_record_id": "post123", "title": "hello", "metadata": {}}
        assert source_fingerprint(row) == source_fingerprint(row)

    def test_different_sources_differ(self):
        from src.pipeline.text_normalizer import source_fingerprint
        r1 = {"id": "1", "source": "instagram", "entity_id": "e", "event_type": "t",
               "occurred_at": "2026", "source_record_id": "r", "title": "t", "metadata": None}
        r2 = dict(r1, source="telegram")
        assert source_fingerprint(r1) != source_fingerprint(r2)

    def test_returns_64_hex_chars(self):
        from src.pipeline.text_normalizer import source_fingerprint
        row = {"id": "1", "source": "s", "entity_id": "e", "event_type": "t",
               "occurred_at": "2026", "source_record_id": "r", "title": "t", "metadata": None}
        result = source_fingerprint(row)
        assert len(result) == 64


# ---------------------------------------------------------------------------
# translation_worker: normalize_translation_language, opus_model_name,
#                     nllb_language_code, translation_max_per_run,
#                     text_version_hash, translation_decision
# ---------------------------------------------------------------------------

class TestNormalizeTranslationLanguage:
    def _n(self, lang, provider="opus"):
        from src.pipeline.translation_worker import normalize_translation_language
        return normalize_translation_language(lang, provider=provider)

    def test_cmn_to_zh(self):
        assert self._n("cmn") == "zh"

    def test_zh_cn_to_zh(self):
        assert self._n("zh-cn") == "zh"

    def test_unknown_lang_passthrough(self):
        assert self._n("fr") == "fr"

    def test_none_returns_und(self):
        assert self._n(None) == "und"

    def test_strips_subtag_for_opus(self):
        # fr-FR → "fr" (opus splits on -)
        assert self._n("fr-FR", provider="opus") == "fr"

    def test_nllb_zh(self):
        assert self._n("zh", provider="nllb") == "zho_Hans"

    def test_nllb_unknown_passthrough(self):
        assert self._n("fr", provider="nllb") == "fr"


class TestOpusModelName:
    def test_zh_en_model(self):
        from src.pipeline.translation_worker import opus_model_name
        name = opus_model_name("zh", "en")
        assert "zh" in name and "en" in name

    def test_returns_string(self):
        from src.pipeline.translation_worker import opus_model_name
        assert isinstance(opus_model_name("id", "en"), str)


class TestNllbLanguageCode:
    def test_en(self):
        from src.pipeline.translation_worker import nllb_language_code
        assert nllb_language_code("en") == "eng_Latn"

    def test_zh(self):
        from src.pipeline.translation_worker import nllb_language_code
        assert nllb_language_code("zh") == "zho_Hans"

    def test_unknown_passthrough(self):
        from src.pipeline.translation_worker import nllb_language_code
        assert nllb_language_code("fr") == "fr"


class TestTranslationMaxPerRun:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("TRANSLATION_MAX_PER_RUN", raising=False)
        from src.pipeline.translation_worker import translation_max_per_run
        assert translation_max_per_run() == 500

    def test_custom(self, monkeypatch):
        monkeypatch.setenv("TRANSLATION_MAX_PER_RUN", "100")
        from src.pipeline.translation_worker import translation_max_per_run
        assert translation_max_per_run() == 100

    def test_minimum_is_1(self, monkeypatch):
        monkeypatch.setenv("TRANSLATION_MAX_PER_RUN", "0")
        from src.pipeline.translation_worker import translation_max_per_run
        assert translation_max_per_run() == 1


class TestTextVersionHash:
    def test_deterministic(self):
        from src.pipeline.translation_worker import text_version_hash
        assert text_version_hash("hello") == text_version_hash("hello")

    def test_different_texts_differ(self):
        from src.pipeline.translation_worker import text_version_hash
        assert text_version_hash("a") != text_version_hash("b")

    def test_returns_40_hex(self):
        from src.pipeline.translation_worker import text_version_hash
        result = text_version_hash("test")
        assert len(result) == 40


class TestTranslationDecision:
    def _d(self, source_language, token_count, watched=False, target="en"):
        from src.pipeline.translation_worker import translation_decision
        return translation_decision(
            source_language=source_language,
            token_count=token_count,
            watched=watched,
            target_language=target,
        )

    def test_english_not_translated(self):
        decision = self._d("en", 10)
        assert decision.should_translate is False

    def test_und_not_translated(self):
        decision = self._d("und", 10)
        assert decision.should_translate is False

    def test_chinese_translated(self):
        decision = self._d("zh", 10)
        assert decision.should_translate is True

    def test_too_short_not_translated(self):
        decision = self._d("zh", 3)
        assert decision.should_translate is False

    def test_too_short_but_watched_translated(self):
        decision = self._d("zh", 3, watched=True)
        assert decision.should_translate is True

    def test_noop_translator_raises(self):
        from src.pipeline.translation_worker import NoopTranslator
        t = NoopTranslator()
        with pytest.raises(RuntimeError):
            t.translate("hello", "zh", "en")


# ---------------------------------------------------------------------------
# api.routes.graph: _relationship_why
# ---------------------------------------------------------------------------

class TestRelationshipWhy:
    def _w(self, relationship_type, sources):
        from src.api.routes.graph import _relationship_why
        return _relationship_why(relationship_type, sources)

    def test_explicit_why(self):
        sources = {"why": "They share a phone number"}
        result = self._w("email_match", sources)
        assert result == "They share a phone number"

    def test_interaction_type(self):
        sources = {"by_type": {"replied": 5, "reacted": 3}}
        result = self._w("interaction", sources)
        assert result is not None
        assert "replied" in result

    def test_social_graph_overlap(self):
        sources = {"shared": 10, "jaccard": 0.45}
        result = self._w("social_graph_overlap", sources)
        assert result is not None
        assert "10" in result

    def test_none_sources_returns_none(self):
        assert self._w("interaction", None) is None

    def test_non_dict_sources_returns_none(self):
        assert self._w("interaction", "not-a-dict") is None

    def test_empty_explicit_why_falls_through(self):
        sources = {"why": "  "}
        result = self._w("unknown_type", sources)
        assert result is None

    def test_temporal_hour_similarity(self):
        sources = {"similarity": 0.95}
        result = self._w("temporal_hour_similarity", sources)
        assert result is not None
        assert "0.95" in result

    def test_group_co_member(self):
        sources = {"groups": ["Family Chat", "Work Group"]}
        result = self._w("telegram_group_co_member", sources)
        assert result is not None
        assert "Family Chat" in result
