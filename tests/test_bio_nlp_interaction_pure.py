"""
QA-lane tests for pure functions in:
- src/pipeline/bio_nlp.py: _decode_meta, extract_tokens, extract_hashtags,
  extract_emojis, detect_language_hint, categorize
- src/pipeline/social_face_link.py: _enabled
- src/pipeline/interaction_graph.py: _jsonb_param, _format_source_query
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.pipeline.bio_nlp import (
    STOPWORDS,
    _decode_meta as bio_decode_meta,
    categorize,
    detect_language_hint,
    extract_emojis,
    extract_hashtags,
    extract_tokens,
)
from src.pipeline.social_face_link import _enabled as face_link_enabled
from src.pipeline.interaction_graph import _format_source_query, _jsonb_param


# ---------------------------------------------------------------------------
# bio_nlp._decode_meta
# ---------------------------------------------------------------------------

class TestBioDecodeMeta:
    def test_dict_passthrough(self):
        assert bio_decode_meta({"a": 1}) == {"a": 1}

    def test_valid_json_string(self):
        assert bio_decode_meta('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_empty(self):
        assert bio_decode_meta("bad{") == {}

    def test_none_returns_empty(self):
        assert bio_decode_meta(None) == {}


# ---------------------------------------------------------------------------
# bio_nlp.extract_tokens
# ---------------------------------------------------------------------------

class TestExtractTokens:
    def test_basic_words(self):
        tokens = extract_tokens("Software engineer at Google")
        assert "software" in tokens
        assert "engineer" in tokens
        assert "google" in tokens

    def test_stopwords_removed(self):
        tokens = extract_tokens("I am a developer")
        assert "i" not in tokens
        assert "am" not in tokens
        assert "a" not in tokens
        assert "developer" in tokens

    def test_urls_stripped(self):
        tokens = extract_tokens("Visit https://example.com for info")
        assert not any("http" in t for t in tokens)

    def test_mentions_stripped(self):
        tokens = extract_tokens("Follow @johndoe today")
        assert not any("johndoe" in t for t in tokens)

    def test_emoji_stripped(self):
        tokens = extract_tokens("Developer 🎉 based in Singapore")
        assert "developer" in tokens
        assert "singapore" in tokens

    def test_single_char_excluded(self):
        tokens = extract_tokens("a b c developer")
        assert "a" not in tokens
        assert "developer" in tokens

    def test_empty_returns_empty(self):
        assert extract_tokens("") == []


# ---------------------------------------------------------------------------
# bio_nlp.extract_hashtags
# ---------------------------------------------------------------------------

class TestExtractHashtags:
    def test_extracts_hashtag(self):
        result = extract_hashtags("Love #python and #coding")
        assert "python" in result
        assert "coding" in result

    def test_lowercased(self):
        result = extract_hashtags("#Python #Coding")
        assert "python" in result
        assert "coding" in result

    def test_no_hashtags_returns_empty(self):
        assert extract_hashtags("no hashtags here") == []

    def test_empty_returns_empty(self):
        assert extract_hashtags("") == []


# ---------------------------------------------------------------------------
# bio_nlp.extract_emojis
# ---------------------------------------------------------------------------

class TestExtractEmojis:
    def test_extracts_emojis(self):
        result = extract_emojis("Hello 🎉 world 🔥")
        assert len(result) >= 1

    def test_no_emojis_returns_empty(self):
        assert extract_emojis("no emojis here") == []

    def test_empty_returns_empty(self):
        assert extract_emojis("") == []


# ---------------------------------------------------------------------------
# bio_nlp.detect_language_hint
# ---------------------------------------------------------------------------

class TestDetectLanguageHint:
    def test_cjk_text(self):
        assert detect_language_hint("今晚一起吃饭") == "cjk"

    def test_latin_text(self):
        assert detect_language_hint("hello world") == "latin"

    def test_mixed_cjk_dominant(self):
        # More CJK than latin chars → cjk
        result = detect_language_hint("一二三四五六七 hi")
        assert result == "cjk"

    def test_no_text_returns_none(self):
        assert detect_language_hint("123 !@#") is None

    def test_empty_returns_none(self):
        assert detect_language_hint("") is None


# ---------------------------------------------------------------------------
# bio_nlp.categorize
# ---------------------------------------------------------------------------

class TestCategorize:
    def test_tech_tokens_detected(self):
        result = categorize(["developer", "python", "backend"])
        assert "tech" in result
        assert result["tech"] >= 1

    def test_fitness_tokens_detected(self):
        result = categorize(["runner", "gym", "training"])
        assert "fitness" in result

    def test_no_match_returns_empty(self):
        result = categorize(["random", "words", "here"])
        assert result == {}

    def test_multiple_categories(self):
        result = categorize(["developer", "runner", "gym"])
        assert "tech" in result
        assert "fitness" in result

    def test_empty_tokens_returns_empty(self):
        assert categorize([]) == {}

    def test_count_is_overlap_size(self):
        result = categorize(["developer", "python", "coding"])
        assert result.get("tech", 0) == 3


# ---------------------------------------------------------------------------
# social_face_link._enabled
# ---------------------------------------------------------------------------

class TestSocialFaceLinkEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("SOCIAL_FACE_LINK_ENABLED", raising=False)
        assert face_link_enabled() is True

    def test_disabled_when_0(self, monkeypatch):
        monkeypatch.setenv("SOCIAL_FACE_LINK_ENABLED", "0")
        assert face_link_enabled() is False

    def test_enabled_when_1(self, monkeypatch):
        monkeypatch.setenv("SOCIAL_FACE_LINK_ENABLED", "1")
        assert face_link_enabled() is True


# ---------------------------------------------------------------------------
# interaction_graph._jsonb_param
# ---------------------------------------------------------------------------

class TestInteractionJsonbParam:
    def test_none_returns_empty_object(self):
        assert _jsonb_param(None) == "{}"

    def test_string_passed_through(self):
        assert _jsonb_param('{"key": "val"}') == '{"key": "val"}'

    def test_dict_serialized(self):
        result = json.loads(_jsonb_param({"a": 1}))
        assert result == {"a": 1}

    def test_non_serializable_uses_str(self):
        result = json.loads(_jsonb_param({"ts": datetime(2024, 1, 1, tzinfo=timezone.utc)}))
        assert "ts" in result


# ---------------------------------------------------------------------------
# interaction_graph._format_source_query
# ---------------------------------------------------------------------------

_SIMPLE_SPEC = {
    "query": "SELECT * FROM t WHERE 1=1 {where_clause}",
    "time_col": "t.created_at",
}

_ANALYZER_SPEC = {
    "query": "SELECT * FROM t WHERE 1=1 {where_clause}",
    "time_col": "t.created_at",
    "db": "analyzer",
}


class TestFormatSourceQuery:
    def test_no_since_empty_where_clause(self):
        sql, params = _format_source_query(_SIMPLE_SPEC, since=None)
        assert "{where_clause}" not in sql
        assert params == []

    def test_with_since_injects_filter(self):
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        sql, params = _format_source_query(_SIMPLE_SPEC, since=since)
        assert "t.created_at" in sql
        assert "$1" in sql
        assert len(params) == 1

    def test_collector_db_strips_tz(self):
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        _, params = _format_source_query(_SIMPLE_SPEC, since=since)
        assert params[0].tzinfo is None

    def test_analyzer_db_keeps_tz(self):
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        _, params = _format_source_query(_ANALYZER_SPEC, since=since)
        assert params[0] == since
