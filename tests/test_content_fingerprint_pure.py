"""
QA-lane tests for pure functions in src/pipeline/content_fingerprint.py.

Covers:
- _decode_meta: dict/str/bytes/invalid coercion
- _tokenize: URL/mention/hashtag/emoji stripping, stopword filtering
- _compute_fingerprint: None when too few tokens, correct stats shape
- _cosine_sim: orthogonal/identical/partial overlap
"""
from __future__ import annotations

import json
import math

import pytest

from src.pipeline.content_fingerprint import (
    STOPWORDS,
    _MIN_TOKENS,
    _compute_fingerprint,
    _cosine_sim,
    _decode_meta,
    _tokenize,
)


# ---------------------------------------------------------------------------
# _decode_meta
# ---------------------------------------------------------------------------

class TestDecodeMeta:
    def test_dict_returned_as_is(self):
        d = {"key": "val"}
        assert _decode_meta(d) == d

    def test_valid_json_string_parsed(self):
        assert _decode_meta('{"a": 1}') == {"a": 1}

    def test_json_array_returns_empty(self):
        assert _decode_meta("[1, 2]") == {}

    def test_invalid_json_returns_empty(self):
        assert _decode_meta("not{json") == {}

    def test_bytes_parsed(self):
        assert _decode_meta(b'{"k": "v"}') == {"k": "v"}

    def test_invalid_bytes_returns_empty(self):
        assert _decode_meta(b"bad bytes") == {}

    def test_none_returns_empty(self):
        assert _decode_meta(None) == {}

    def test_integer_returns_empty(self):
        assert _decode_meta(42) == {}


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_basic_words_tokenized(self):
        tokens = _tokenize("hello world this is a test")
        # "this" "is" "a" are stopwords; "hello" "world" "test" survive
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_stopwords_removed(self):
        tokens = _tokenize("i am the one who knocks")
        for word in ["i", "am", "the"]:
            assert word not in tokens

    def test_urls_stripped(self):
        tokens = _tokenize("visit https://example.com for more")
        assert not any("http" in t or "example" in t for t in tokens)

    def test_mentions_stripped(self):
        tokens = _tokenize("thanks @alice for the help")
        assert not any("alice" in t for t in tokens)

    def test_hashtags_stripped(self):
        tokens = _tokenize("love #python and #coding today")
        assert not any("python" in t for t in tokens)
        assert not any("coding" in t for t in tokens)

    def test_emoji_stripped(self):
        tokens = _tokenize("great post 🎉🔥 really good")
        assert all(len(t) <= 20 for t in tokens)
        assert "great" in tokens
        assert "post" in tokens

    def test_single_char_words_excluded(self):
        # regex \b[a-z]{2,20}\b — single chars excluded
        tokens = _tokenize("a b c good")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "good" in tokens

    def test_long_words_excluded(self):
        # > 20 chars excluded by regex
        long_word = "a" * 21
        tokens = _tokenize(long_word)
        assert long_word.lower() not in tokens

    def test_lowercases_all_tokens(self):
        tokens = _tokenize("Hello WORLD Test")
        assert "hello" in tokens
        assert "world" in tokens

    def test_empty_string_returns_empty(self):
        assert _tokenize("") == []


# ---------------------------------------------------------------------------
# _compute_fingerprint
# ---------------------------------------------------------------------------

class TestComputeFingerprint:
    def _rich_texts(self, n=10):
        """Generate enough distinct text to clear _MIN_TOKENS=50."""
        # Pure alpha words only — digits excluded by \b[a-z]{2,20}\b regex
        vocab = [
            "python", "coding", "developer", "software", "engineering",
            "machine", "learning", "analysis", "database", "framework",
            "testing", "deployment", "architecture", "performance", "security",
        ]
        text = " ".join(vocab)
        return [text] * n

    def test_returns_none_when_too_few_tokens(self):
        # One very short text → below _MIN_TOKENS
        result = _compute_fingerprint(["hi there"])
        assert result is None

    def test_returns_dict_with_expected_keys_when_sufficient_text(self):
        texts = self._rich_texts(10)
        result = _compute_fingerprint(texts)
        assert result is not None
        for key in ("token_count", "vocab_size", "vocab_richness",
                    "avg_post_length", "top_words", "post_count"):
            assert key in result

    def test_post_count_matches_input(self):
        texts = self._rich_texts(7)
        result = _compute_fingerprint(texts)
        assert result is not None
        assert result["post_count"] == 7

    def test_token_count_positive(self):
        texts = self._rich_texts(10)
        result = _compute_fingerprint(texts)
        assert result is not None
        assert result["token_count"] >= _MIN_TOKENS

    def test_vocab_richness_between_0_and_1(self):
        texts = self._rich_texts(10)
        result = _compute_fingerprint(texts)
        assert result is not None
        assert 0.0 <= result["vocab_richness"] <= 1.0

    def test_top_words_is_list(self):
        texts = self._rich_texts(10)
        result = _compute_fingerprint(texts)
        assert result is not None
        assert isinstance(result["top_words"], list)

    def test_empty_list_returns_none(self):
        assert _compute_fingerprint([]) is None


# ---------------------------------------------------------------------------
# _cosine_sim
# ---------------------------------------------------------------------------

class TestCosineSim:
    def test_identical_dicts_give_1(self):
        a = {"hello": 3, "world": 2}
        assert abs(_cosine_sim(a, a) - 1.0) < 1e-9

    def test_orthogonal_dicts_give_0(self):
        a = {"hello": 1}
        b = {"world": 1}
        assert _cosine_sim(a, b) == 0.0

    def test_empty_dict_gives_0(self):
        assert _cosine_sim({}, {"hello": 1}) == 0.0
        assert _cosine_sim({"hello": 1}, {}) == 0.0

    def test_partial_overlap_between_0_and_1(self):
        a = {"hello": 2, "world": 1}
        b = {"hello": 2, "foo": 3}
        sim = _cosine_sim(a, b)
        assert 0.0 < sim < 1.0

    def test_symmetry(self):
        a = {"hello": 3, "world": 2}
        b = {"hello": 1, "python": 4}
        assert abs(_cosine_sim(a, b) - _cosine_sim(b, a)) < 1e-9

    def test_scaled_dicts_give_same_similarity(self):
        # Scaling all values by a constant shouldn't change cosine sim
        a = {"hello": 1, "world": 2}
        b = {"hello": 2, "world": 4}
        assert abs(_cosine_sim(a, a) - _cosine_sim(b, b)) < 1e-9
        assert abs(_cosine_sim(a, b) - 1.0) < 1e-9
