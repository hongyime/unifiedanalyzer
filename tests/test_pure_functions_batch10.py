"""
Pure-function tests — batch 10.

Covers previously untested modules with no DB or I/O:
- pipeline.beeper_bridge: _digits, parse_native_sender
- pipeline.content_fingerprint: _decode_meta, _tokenize, _compute_fingerprint, _cosine_sim
- pipeline.temporal_correlation: _decode_meta, _cosine, _hour_idf_weights, _weighted_cosine
- pipeline.text_embedder: _default_model_dir
"""
from __future__ import annotations

import json
import os
from math import sqrt


# ---------------------------------------------------------------------------
# beeper_bridge: _digits, parse_native_sender
# ---------------------------------------------------------------------------

class TestDigits:
    def _d(self, s):
        from src.pipeline.beeper_bridge import _digits
        return _digits(s)

    def test_extracts_digits(self):
        assert self._d("+65 9123-4567") == "6591234567"

    def test_pure_digits_passthrough(self):
        assert self._d("12345") == "12345"

    def test_empty_string(self):
        assert self._d("") == ""

    def test_no_digits(self):
        assert self._d("abc") == ""


class TestParseNativeSender:
    def _p(self, network, sender_id, display=None):
        from src.pipeline.beeper_bridge import parse_native_sender
        return parse_native_sender(network, sender_id, display)

    def test_telegram_valid(self):
        result = self._p("Telegram", "@telegram_123456:beeper.local")
        assert result == ("telegram", "123456", None, None)

    def test_telegram_invalid_returns_none(self):
        assert self._p("Telegram", "@notmatch:beeper.local") is None

    def test_instagram_valid(self):
        result = self._p("Instagram", "@instagram_789:beeper.local")
        assert result == ("instagram", "789", None, None)

    def test_instagram_go_prefix(self):
        result = self._p("Instagram", "@instagramgo_456:beeper.local")
        assert result == ("instagram", "456", None, None)

    def test_whatsapp_direct(self):
        result = self._p("WhatsApp", "@whatsapp_6591234567:beeper.local", "Alice")
        assert result is not None
        assert result[0] == "whatsapp"
        assert "6591234567" in result[1]
        assert result[3] == "Alice"  # non-phone display stored as name

    def test_whatsapp_lid_phone_display(self):
        result = self._p("WhatsApp", "@whatsapp_lid-98765:beeper.local", "+6591234567")
        assert result is not None
        assert result[0] == "whatsapp"
        assert "6591234567" in result[1]

    def test_whatsapp_lid_name_display(self):
        result = self._p("WhatsApp", "@whatsapp_lid-98765:beeper.local", "John Smith")
        assert result is not None
        assert result[0] == "__lid__"
        assert result[1] == "98765"

    def test_unknown_network_returns_none(self):
        assert self._p("Discord", "@discord_123:beeper.local") is None

    def test_display_passed_through_telegram(self):
        result = self._p("Telegram", "@telegram_99:beeper.local", "Alice")
        assert result == ("telegram", "99", "Alice", "Alice")


# ---------------------------------------------------------------------------
# content_fingerprint: _decode_meta, _tokenize, _compute_fingerprint, _cosine_sim
# ---------------------------------------------------------------------------

class TestContentFingerprintDecodeMeta:
    def _d(self, raw):
        from src.pipeline.content_fingerprint import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        assert self._d({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._d('{"a": 1}') == {"a": 1}

    def test_invalid_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


class TestTokenize:
    def _t(self, text):
        from src.pipeline.content_fingerprint import _tokenize
        return _tokenize(text)

    def test_basic_words(self):
        tokens = self._t("Hello world this is a test")
        assert "hello" in tokens or "world" in tokens or "test" in tokens

    def test_strips_urls(self):
        tokens = self._t("visit https://example.com for details")
        assert not any("example" in t for t in tokens)

    def test_strips_mentions(self):
        tokens = self._t("follow @alice for updates")
        assert "alice" not in tokens

    def test_strips_hashtags(self):
        tokens = self._t("love this #travel post")
        assert "travel" not in tokens

    def test_stopwords_removed(self):
        tokens = self._t("the and a is to")
        assert tokens == []

    def test_short_words_excluded(self):
        # single chars filtered by \b[a-z]{2,20}\b
        tokens = self._t("a b c de")
        assert "a" not in tokens
        assert "b" not in tokens

    def test_returns_lowercase(self):
        tokens = self._t("Python JavaScript TypeScript")
        assert "python" in tokens
        assert "javascript" in tokens


class TestComputeFingerprint:
    def _f(self, texts):
        from src.pipeline.content_fingerprint import _compute_fingerprint
        return _compute_fingerprint(texts)

    def _enough_text(self):
        # 50+ tokens required — use a paragraph with diverse words
        return ["software engineering coding python javascript typescript golang rust java kotlin swift " * 6]

    def test_returns_none_below_min_tokens(self):
        assert self._f(["short text"]) is None

    def test_returns_dict_with_enough_tokens(self):
        result = self._f(self._enough_text())
        assert result is not None
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        result = self._f(self._enough_text())
        assert result is not None
        for key in ("token_count", "vocab_size", "vocab_richness", "top_words", "post_count"):
            assert key in result

    def test_post_count_matches_input(self):
        texts = self._enough_text() + self._enough_text()
        result = self._f(texts)
        assert result is not None
        assert result["post_count"] == 2

    def test_empty_list(self):
        # no texts → zero tokens → below min
        assert self._f([]) is None


class TestContentFingerprintCosineSim:
    def _c(self, a, b):
        from src.pipeline.content_fingerprint import _cosine_sim
        return _cosine_sim(a, b)

    def test_identical_dicts(self):
        d = {"apple": 3, "banana": 2}
        assert abs(self._c(d, d) - 1.0) < 1e-9

    def test_disjoint_dicts(self):
        assert self._c({"a": 1}, {"b": 1}) == 0.0

    def test_empty_dicts(self):
        assert self._c({}, {}) == 0.0

    def test_partial_overlap(self):
        # a=(1,1,0), b=(0,1,1) → dot=1, mag=√2 each → 0.5
        result = self._c({"x": 1, "y": 1}, {"y": 1, "z": 1})
        assert abs(result - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# temporal_correlation: _decode_meta, _cosine, _hour_idf_weights, _weighted_cosine
# ---------------------------------------------------------------------------

class TestTemporalDecodeMeta:
    def _d(self, raw):
        from src.pipeline.temporal_correlation import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        assert self._d({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._d('{"x": 1}') == {"x": 1}

    def test_json_bytes(self):
        assert self._d(b'{"y": 2}') == {"y": 2}

    def test_invalid_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


class TestTemporalCosine:
    def _c(self, a, b):
        from src.pipeline.temporal_correlation import _cosine
        return _cosine(a, b)

    def test_identical_vectors(self):
        v = [1.0] * 24
        assert abs(self._c(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        a = [1.0] + [0.0] * 23
        b = [0.0] + [1.0] + [0.0] * 22
        assert self._c(a, b) == 0.0

    def test_zero_magnitude(self):
        a = [0.0] * 24
        b = [1.0] * 24
        assert self._c(a, b) == 0.0

    def test_result_between_0_and_1(self):
        import random
        random.seed(42)
        a = [random.random() for _ in range(24)]
        b = [random.random() for _ in range(24)]
        result = self._c(a, b)
        assert 0.0 <= result <= 1.0


class TestHourIdfWeights:
    def _w(self, hour_dists):
        from src.pipeline.temporal_correlation import _hour_idf_weights
        return _hour_idf_weights(hour_dists)

    def test_returns_24_weights(self):
        dists = {"e1": [1.0] * 24}
        weights = self._w(dists)
        assert len(weights) == 24

    def test_all_positive(self):
        dists = {"e1": [1.0] * 24, "e2": [0.5] * 24}
        weights = self._w(dists)
        assert all(w > 0 for w in weights)

    def test_rare_hour_higher_weight(self):
        # Hour 0 used by 1 entity, hour 1 used by all 10
        n = 10
        dists = {}
        for i in range(n):
            dist = [0.0] * 24
            dist[1] = 1.0  # all use hour 1
            if i == 0:
                dist[0] = 1.0  # only entity 0 uses hour 0
            dists[f"e{i}"] = dist
        weights = self._w(dists)
        # Rare hour 0 should have higher weight than common hour 1
        assert weights[0] > weights[1]

    def test_empty_dists(self):
        weights = self._w({})
        assert len(weights) == 24
        assert all(w >= 0.05 for w in weights)


class TestWeightedCosine:
    def _wc(self, a, b, w):
        from src.pipeline.temporal_correlation import _weighted_cosine
        return _weighted_cosine(a, b, w)

    def test_identical_with_uniform_weights(self):
        v = [1.0] * 24
        w = [1.0] * 24
        assert abs(self._wc(v, v, w) - 1.0) < 1e-9

    def test_zero_weight_hour_ignored(self):
        a = [1.0] + [0.0] * 23
        b = [0.0] * 24
        b[0] = 1.0
        w = [0.0] * 24  # zero weight everywhere
        # After scaling both become zero → cosine = 0
        assert self._wc(a, b, w) == 0.0


# ---------------------------------------------------------------------------
# text_embedder: _default_model_dir
# ---------------------------------------------------------------------------

class TestDefaultModelDir:
    def test_uses_override_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TEXT_EMBED_MODEL_PATH", str(tmp_path))
        from src.pipeline.text_embedder import _default_model_dir
        result = _default_model_dir()
        assert result == tmp_path.resolve()

    def test_default_path_contains_text_embedder(self, monkeypatch):
        monkeypatch.delenv("TEXT_EMBED_MODEL_PATH", raising=False)
        monkeypatch.delenv("MEDIA_DERIVED_PATH", raising=False)
        from src.pipeline.text_embedder import _default_model_dir
        result = _default_model_dir()
        assert "text_embedder" in str(result)

    def test_custom_media_derived_path(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TEXT_EMBED_MODEL_PATH", raising=False)
        monkeypatch.setenv("MEDIA_DERIVED_PATH", str(tmp_path))
        from src.pipeline.text_embedder import _default_model_dir
        result = _default_model_dir()
        assert str(tmp_path) in str(result)
        assert "text_embedder" in str(result)
