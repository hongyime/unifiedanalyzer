"""
Pure-function tests — batch 9.

Covers previously untested modules with no DB or I/O:
- pipeline.bio_mention: _normalize_mention, _extract_mentions, MENTION_RE, URL_RE
- pipeline.face_associations: _normalize_embedding (pure numpy, no DB)
- pipeline.face_pair_signals: _pair_key
- pipeline.social_face_link: _enabled
- pipeline.interaction_graph: _resolve_entity, _jsonb_param, _format_source_query
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# bio_mention: _normalize_mention, _extract_mentions
# ---------------------------------------------------------------------------

class TestNormalizeMention:
    def _n(self, raw):
        from src.pipeline.bio_mention import _normalize_mention
        return _normalize_mention(raw)

    def test_basic_handle(self):
        assert self._n("alice") == "alice"

    def test_strips_at_prefix(self):
        assert self._n("@alice") == "alice"

    def test_lowercases(self):
        assert self._n("Alice") == "alice"

    def test_strips_dots_underscores_dashes(self):
        # "al.ic_e" → strip ._- → "alice"
        assert self._n("al.ic_e") == "alice"

    def test_strips_trailing_digits(self):
        # "alice123" → strip trailing digits → "alice"
        assert self._n("alice123") == "alice"

    def test_too_short_returns_none(self):
        # "al" is 2 chars < MIN_LENGTH=3 → None
        assert self._n("al") is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_none_returns_none(self):
        assert self._n(None) is None

    def test_contains_space_returns_none(self):
        assert self._n("alice bob") is None

    def test_generic_user_pattern_returns_none(self):
        # "user1" matches _DEFAULT_USERNAME_RE → None
        assert self._n("user1") is None

    def test_generic_user_no_digits_returns_none(self):
        assert self._n("user") is None


class TestExtractMentions:
    def _e(self, bio):
        from src.pipeline.bio_mention import _extract_mentions
        return _extract_mentions(bio)

    def test_single_mention(self):
        result = self._e("follow me @alice on instagram")
        assert "alice" in result

    def test_multiple_mentions(self):
        result = self._e("say hi to @alice and @bob today")
        assert "alice" in result
        assert "bob" in result

    def test_deduplicates(self):
        result = self._e("@alice @alice @alice")
        assert result.count("alice") == 1

    def test_url_mentions_stripped(self):
        # URL containing @ should not produce a mention
        result = self._e("visit https://example.com/@page for info")
        assert result == [] or "page" not in result

    def test_empty_bio(self):
        assert self._e("") == []

    def test_no_mentions(self):
        assert self._e("just plain text, nothing here") == []

    def test_short_handle_filtered(self):
        # @ab → 2 chars → filtered by _normalize_mention
        result = self._e("@ab is short")
        assert "ab" not in result

    def test_order_preserved(self):
        result = self._e("@alice then @bob then @charlie")
        assert result == ["alice", "bob", "charlie"]


# ---------------------------------------------------------------------------
# face_associations: _normalize_embedding
# ---------------------------------------------------------------------------

class TestNormalizeEmbedding:
    def _n(self, text):
        from src.pipeline.face_associations import _normalize_embedding
        return _normalize_embedding(text)

    def test_unit_norm_output(self):
        import numpy as np
        # A simple 3-dim vector
        v = [1.0, 0.0, 0.0]
        result = self._n(json.dumps(v))
        assert result is not None
        assert abs(float(np.linalg.norm(result)) - 1.0) < 1e-6

    def test_zero_vector_returns_none(self):
        result = self._n(json.dumps([0.0, 0.0, 0.0]))
        assert result is None

    def test_non_unit_input_normalised(self):
        import numpy as np
        v = [3.0, 4.0]  # norm=5
        result = self._n(json.dumps(v))
        assert result is not None
        assert abs(float(np.linalg.norm(result)) - 1.0) < 1e-6
        assert abs(float(result[0]) - 0.6) < 1e-6
        assert abs(float(result[1]) - 0.8) < 1e-6

    def test_float32_dtype(self):
        import numpy as np
        result = self._n(json.dumps([1.0, 0.0]))
        assert result.dtype == np.float32

    def test_negative_components_ok(self):
        import numpy as np
        result = self._n(json.dumps([-1.0, 0.0, 0.0]))
        assert result is not None
        assert abs(float(np.linalg.norm(result)) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# face_pair_signals: _pair_key
# ---------------------------------------------------------------------------

class TestPairKey:
    def _p(self, a, b):
        from src.pipeline.face_pair_signals import _pair_key
        return _pair_key(a, b)

    def test_already_sorted(self):
        assert self._p("aaa", "bbb") == ("aaa", "bbb")

    def test_reversed_sorted(self):
        assert self._p("bbb", "aaa") == ("aaa", "bbb")

    def test_equal_values(self):
        assert self._p("x", "x") == ("x", "x")

    def test_uuid_like(self):
        a = "00000000-0000-0000-0000-000000000001"
        b = "00000000-0000-0000-0000-000000000002"
        assert self._p(b, a) == (a, b)

    def test_symmetric(self):
        assert self._p("a", "b") == self._p("b", "a")


# ---------------------------------------------------------------------------
# social_face_link: _enabled
# ---------------------------------------------------------------------------

class TestSocialFaceLinkEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("SOCIAL_FACE_LINK_ENABLED", raising=False)
        from src.pipeline.social_face_link import _enabled
        assert _enabled() is True

    def test_disabled_when_0(self, monkeypatch):
        monkeypatch.setenv("SOCIAL_FACE_LINK_ENABLED", "0")
        from src.pipeline.social_face_link import _enabled
        assert _enabled() is False

    def test_enabled_when_1(self, monkeypatch):
        monkeypatch.setenv("SOCIAL_FACE_LINK_ENABLED", "1")
        from src.pipeline.social_face_link import _enabled
        assert _enabled() is True


# ---------------------------------------------------------------------------
# interaction_graph: _resolve_entity, _jsonb_param, _format_source_query
# ---------------------------------------------------------------------------

class TestResolveEntity:
    def _r(self, lookup, source, *refs):
        from src.pipeline.interaction_graph import _resolve_entity
        return _resolve_entity(lookup, source, *refs)

    def test_exact_match(self):
        lookup = {("instagram", "alice"): "eid-1"}
        assert self._r(lookup, "instagram", "alice") == "eid-1"

    def test_case_insensitive_match(self):
        lookup = {("instagram", "alice"): "eid-1"}
        assert self._r(lookup, "instagram", "ALICE") == "eid-1"

    def test_first_ref_wins(self):
        lookup = {("instagram", "alice"): "eid-1", ("instagram", "bob"): "eid-2"}
        assert self._r(lookup, "instagram", "alice", "bob") == "eid-1"

    def test_fallback_to_second_ref(self):
        lookup = {("instagram", "bob"): "eid-2"}
        assert self._r(lookup, "instagram", "missing", "bob") == "eid-2"

    def test_no_match_returns_none(self):
        assert self._r({}, "instagram", "ghost") is None

    def test_empty_ref_skipped(self):
        lookup = {("instagram", "alice"): "eid-1"}
        assert self._r(lookup, "instagram", "", "alice") == "eid-1"

    def test_none_ref_skipped(self):
        lookup = {("instagram", "alice"): "eid-1"}
        assert self._r(lookup, "instagram", None, "alice") == "eid-1"

    def test_wrong_source_no_match(self):
        lookup = {("instagram", "alice"): "eid-1"}
        assert self._r(lookup, "telegram", "alice") is None


class TestJsonbParamInteraction:
    def _j(self, raw):
        from src.pipeline.interaction_graph import _jsonb_param
        return _jsonb_param(raw)

    def test_none_returns_empty_object(self):
        assert self._j(None) == "{}"

    def test_string_passthrough(self):
        s = '{"key": "val"}'
        assert self._j(s) == s

    def test_dict_serialized(self):
        result = self._j({"a": 1})
        assert json.loads(result) == {"a": 1}

    def test_non_serializable_uses_str_default(self):
        from datetime import datetime
        result = self._j({"ts": datetime(2026, 1, 1)})
        parsed = json.loads(result)
        assert "ts" in parsed


class TestFormatSourceQuery:
    def _spec(self, query="{where_clause}", time_col="t.created_at", db=None):
        spec = {"query": query, "time_col": time_col}
        if db:
            spec["db"] = db
        return spec

    def test_no_since_empty_where(self):
        from src.pipeline.interaction_graph import _format_source_query
        spec = self._spec()
        q, params = _format_source_query(spec, None)
        assert params == []
        assert "{where_clause}" not in q  # formatted to ""

    def test_with_since_adds_param(self):
        from src.pipeline.interaction_graph import _format_source_query
        spec = self._spec()
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        q, params = _format_source_query(spec, since)
        assert len(params) == 1

    def test_analyzer_db_preserves_tz(self):
        from src.pipeline.interaction_graph import _format_source_query
        spec = self._spec(db="analyzer")
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _, params = _format_source_query(spec, since)
        # analyzer DB spec → param passed as-is (with tzinfo)
        assert params[0].tzinfo is not None

    def test_collector_db_strips_tz(self):
        from src.pipeline.interaction_graph import _format_source_query
        spec = self._spec()  # no db="analyzer" → collector path
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _, params = _format_source_query(spec, since)
        # collector path → tzinfo stripped
        assert params[0].tzinfo is None
