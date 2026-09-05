from datetime import datetime, timezone

from src.pipeline.text_normalizer import (
    NormalizedTimelineText,
    _clean,
    _coerce_json,
    _domain_from_url,
    _is_emoji,
    _select_metadata,
    build_canonical_timeline_text,
    normalize_social_text,
    source_fingerprint,
    text_sha1,
)


def _event(metadata):
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "entity_id": "00000000-0000-0000-0000-000000000002",
        "occurred_at": datetime(2026, 8, 8, tzinfo=timezone.utc),
        "source": "instagram",
        "event_type": "CONTENT_PUBLISHED",
        "source_record_id": "post-1",
        "title": "Launch post from @bryan",
        "detail": "Extra timeline detail",
        "metadata": metadata,
    }


def test_canonical_text_preserves_osint_tokens_from_collector_metadata():
    normalized = build_canonical_timeline_text(
        _event({
            "caption": "Met #OSINT people at https://example.com/path",
            "target_preview": "reply preview",
            "location_name": "Marina Bay",
            "mentions": ["analyst_one"],
            "hashtags": ["investigation"],
            "ignored_blob": {"large": "not indexed"},
        })
    )

    assert "Launch post from @bryan" in normalized.canonical_text
    assert "Met #OSINT people" in normalized.canonical_text
    assert "reply preview" in normalized.canonical_text
    assert "Marina Bay" in normalized.canonical_text
    assert normalized.selected_metadata["caption"].startswith("Met #OSINT")
    assert "ignored_blob" not in normalized.selected_metadata
    assert normalized.mention_count == 1
    assert normalized.hashtag_count == 1
    assert normalized.url_count == 1
    assert normalized.domain_count == 1
    assert normalized.token_count > 0
    assert len(normalized.text_sha1) == 40


def test_source_fingerprint_changes_when_text_source_changes():
    first = _event({"caption": "first caption"})
    second = _event({"caption": "second caption"})

    assert source_fingerprint(first) != source_fingerprint(second)
    assert source_fingerprint(first) == source_fingerprint(_event({"caption": "first caption"}))


def test_canonical_text_truncates_with_flag():
    normalized = build_canonical_timeline_text(
        _event({"caption": "x" * 100}),
        max_chars=20,
    )

    assert len(normalized.canonical_text) <= 20
    assert normalized.flags["truncated"] is True


# ---------------------------------------------------------------------------
# text_sha1
# ---------------------------------------------------------------------------

class TestTextSha1:
    def test_known_value(self):
        import hashlib
        expected = hashlib.sha1("hello".encode()).hexdigest()
        assert text_sha1("hello") == expected

    def test_empty_string(self):
        import hashlib
        assert text_sha1("") == hashlib.sha1(b"").hexdigest()

    def test_none_treated_as_empty(self):
        assert text_sha1(None) == text_sha1("")

    def test_different_inputs_produce_different_hashes(self):
        assert text_sha1("abc") != text_sha1("def")


# ---------------------------------------------------------------------------
# normalize_social_text
# ---------------------------------------------------------------------------

class TestNormalizeSocialText:
    def test_none_returns_empty(self):
        assert normalize_social_text(None) == ""

    def test_strips_leading_trailing_whitespace(self):
        assert normalize_social_text("  hello  ") == "hello"

    def test_collapses_horizontal_whitespace(self):
        assert normalize_social_text("hello   world") == "hello world"

    def test_crlf_normalized(self):
        assert normalize_social_text("line1\r\nline2") == "line1\nline2"

    def test_triple_newlines_collapsed_to_double(self):
        assert normalize_social_text("a\n\n\n\nb") == "a\n\nb"

    def test_truncates_at_max_chars(self):
        result = normalize_social_text("a" * 20, max_chars=10)
        assert len(result) <= 10

    def test_no_truncation_when_max_chars_zero(self):
        text = "a" * 100
        assert normalize_social_text(text, max_chars=0) == text

    def test_nfkc_ligature_normalization(self):
        # \uFB01 (fi ligature) -> 'fi'
        assert normalize_social_text("\uFB01ne") == "fine"


# ---------------------------------------------------------------------------
# _coerce_json
# ---------------------------------------------------------------------------

class TestCoerceJson:
    def test_none_returns_empty_dict(self):
        assert _coerce_json(None) == {}

    def test_dict_passthrough(self):
        assert _coerce_json({"a": 1}) == {"a": 1}

    def test_valid_json_string_parsed(self):
        assert _coerce_json('{"a": 1}') == {"a": 1}

    def test_invalid_json_wrapped_as_raw(self):
        assert "raw" in _coerce_json("not-json{")

    def test_json_array_wrapped_as_raw(self):
        assert "raw" in _coerce_json("[1,2]")

    def test_bytes_decoded_and_parsed(self):
        assert _coerce_json(b'{"k": "v"}') == {"k": "v"}

    def test_other_type_wrapped_as_raw(self):
        assert _coerce_json(42) == {"raw": 42}


# ---------------------------------------------------------------------------
# _select_metadata
# ---------------------------------------------------------------------------

class TestSelectMetadata:
    def test_empty_returns_empty(self):
        assert _select_metadata({}) == {}

    def test_allowed_key_kept(self):
        assert _select_metadata({"caption": "hi"}) == {"caption": "hi"}

    def test_unknown_key_excluded(self):
        result = _select_metadata({"caption": "hi", "unknown": "no"})
        assert "unknown" not in result

    def test_none_value_excluded(self):
        assert _select_metadata({"caption": None}) == {}

    def test_empty_string_excluded(self):
        assert _select_metadata({"caption": ""}) == {}

    def test_empty_list_excluded(self):
        assert _select_metadata({"hashtags": []}) == {}

    def test_non_empty_list_kept(self):
        assert _select_metadata({"hashtags": ["#py"]}) == {"hashtags": ["#py"]}


# ---------------------------------------------------------------------------
# _domain_from_url
# ---------------------------------------------------------------------------

class TestDomainFromUrl:
    def test_simple_domain(self):
        assert _domain_from_url("https://example.com/path") == "example.com"

    def test_strips_www(self):
        assert _domain_from_url("https://www.example.com") == "example.com"

    def test_lowercases(self):
        assert _domain_from_url("https://Example.COM") == "example.com"

    def test_no_host_returns_none(self):
        assert _domain_from_url("not-a-url") is None


# ---------------------------------------------------------------------------
# _is_emoji
# ---------------------------------------------------------------------------

class TestIsEmoji:
    def test_misc_symbol_emoji(self):
        assert _is_emoji("\u2600") is True  # ☀

    def test_supplemental_emoji(self):
        assert _is_emoji("\U0001F389") is True  # 🎉

    def test_regular_letter_not_emoji(self):
        assert _is_emoji("A") is False

    def test_digit_not_emoji(self):
        assert _is_emoji("5") is False


# ---------------------------------------------------------------------------
# _clean
# ---------------------------------------------------------------------------

class TestClean:
    def test_none_returns_none(self):
        assert _clean(None) is None

    def test_empty_string_returns_none(self):
        assert _clean("") is None

    def test_whitespace_only_returns_none(self):
        assert _clean("   ") is None

    def test_strips_whitespace(self):
        assert _clean("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert _clean(42) == "42"
