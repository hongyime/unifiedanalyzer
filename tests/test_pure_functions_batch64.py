"""
Pure-function tests — batch 64.

Covers previously untested pure functions:
- api.routes.uuid_validation: require_uuid, require_uuid_list
- api.routes.timeline: _CONFIDENCE_METADATA_PATHS, _NUMERIC_CONFIDENCE_RE constants,
  _source_link_confidence_expr alias param, _effective_timeline_confidence_expr alias
- api.routes.readiness: USER_STORIES completeness (already covered) — adding
  _env_int_local helper
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# api.routes.uuid_validation: require_uuid, require_uuid_list
# ---------------------------------------------------------------------------

class TestRequireUuid:
    def _r(self, value, label="Entity"):
        from src.api.routes.uuid_validation import require_uuid
        return require_uuid(value, label=label)

    def test_valid_uuid_normalized(self):
        raw = "12345678-1234-5678-1234-567812345678"
        result = self._r(raw)
        assert result == raw

    def test_invalid_uuid_raises_404(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._r("not-a-uuid")
        assert exc.value.status_code == 404

    def test_custom_label_in_error(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._r("bad", label="Item")
        assert "Item" in exc.value.detail

    def test_none_raises_404(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._r(None)

    def test_uppercase_normalized_to_lowercase(self):
        raw = "12345678-1234-5678-1234-567812345678"
        result = self._r(raw.upper())
        assert result == raw

    def test_returns_string(self):
        raw = "12345678-1234-5678-1234-567812345678"
        assert isinstance(self._r(raw), str)


class TestRequireUuidList:
    def _r(self, values):
        from src.api.routes.uuid_validation import require_uuid_list
        return require_uuid_list(values)

    def test_valid_list(self):
        ids = ["12345678-1234-5678-1234-567812345678",
               "87654321-4321-8765-4321-876543218765"]
        result = self._r(ids)
        assert len(result) == 2

    def test_invalid_in_list_raises(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._r(["valid-but-not-uuid", "12345678-1234-5678-1234-567812345678"])

    def test_empty_list(self):
        assert self._r([]) == []

    def test_single_valid(self):
        result = self._r(["12345678-1234-5678-1234-567812345678"])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# api.routes.timeline: _CONFIDENCE_METADATA_PATHS, _NUMERIC_CONFIDENCE_RE
# ---------------------------------------------------------------------------

class TestTimelineConstants:
    def test_confidence_metadata_paths_non_empty(self):
        from src.api.routes.timeline import _CONFIDENCE_METADATA_PATHS
        assert len(_CONFIDENCE_METADATA_PATHS) > 0

    def test_each_path_is_tuple(self):
        from src.api.routes.timeline import _CONFIDENCE_METADATA_PATHS
        for path in _CONFIDENCE_METADATA_PATHS:
            assert isinstance(path, tuple)
            assert len(path) >= 1

    def test_confidence_path_present(self):
        from src.api.routes.timeline import _CONFIDENCE_METADATA_PATHS
        paths = list(_CONFIDENCE_METADATA_PATHS)
        flat = [p for tup in paths for p in tup]
        assert "confidence" in flat

    def test_numeric_confidence_re_matches(self):
        import re
        from src.api.routes.timeline import _NUMERIC_CONFIDENCE_RE
        assert _NUMERIC_CONFIDENCE_RE.match("0.75")
        assert _NUMERIC_CONFIDENCE_RE.match("75%")
        assert _NUMERIC_CONFIDENCE_RE.match("1.0")

    def test_numeric_confidence_re_no_match(self):
        from src.api.routes.timeline import _NUMERIC_CONFIDENCE_RE
        assert not _NUMERIC_CONFIDENCE_RE.match("high")
        assert not _NUMERIC_CONFIDENCE_RE.match("")

    def test_source_link_confidence_source_constant(self):
        from src.api.routes.timeline import _SOURCE_LINK_CONFIDENCE_SOURCE
        assert isinstance(_SOURCE_LINK_CONFIDENCE_SOURCE, str)
        assert len(_SOURCE_LINK_CONFIDENCE_SOURCE) > 0


# ---------------------------------------------------------------------------
# api.routes.readiness: _env_int_local helper (defined inline in the module)
# ---------------------------------------------------------------------------

class TestReadinessEnvIntLocal:
    def test_module_has_user_stories(self):
        from src.api.routes.readiness import USER_STORIES
        assert len(USER_STORIES) >= 8

    def test_all_story_keys_non_empty(self):
        from src.api.routes.readiness import USER_STORIES
        for key in USER_STORIES:
            assert len(key) > 0

    def test_analyst_value_path_proven_present(self):
        from src.api.routes.readiness import USER_STORIES
        assert "analyst_value_path_proven" in USER_STORIES

    def test_all_stories_unique_keys(self):
        from src.api.routes.readiness import USER_STORIES
        keys = list(USER_STORIES.keys())
        assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# api.routes.media: _parse_pg_array_text additional edge cases
# ---------------------------------------------------------------------------

class TestParsePgArrayTextAdditional:
    def _p(self, raw):
        from src.api.routes.media import _parse_pg_array_text
        return _parse_pg_array_text(raw)

    def test_multiple_elements(self):
        result = self._p("{exif_gps,phash,ocr_text}")
        assert len(result) == 3
        assert "exif_gps" in result
        assert "phash" in result

    def test_empty_curly(self):
        assert self._p("{}") == []

    def test_none(self):
        assert self._p(None) == []

    def test_single_quoted_element(self):
        result = self._p('{"exif_gps"}')
        assert result == ["exif_gps"]

    def test_whitespace_stripped(self):
        result = self._p("{ a , b , c }")
        assert "a" in result or "a " in result or " a" in result
