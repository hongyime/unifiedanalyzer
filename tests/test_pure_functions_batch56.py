"""
Pure-function tests — batch 56.

Covers previously untested pure functions:
- api.routes.face_search: _decode_image_value
- api.routes.graph: _decode_polyline, _parse_latlng (already in batch45),
  _caption_mentions_place, _as_latlng_list
"""
from __future__ import annotations

import base64
import pytest


# ---------------------------------------------------------------------------
# api.routes.face_search: _decode_image_value
# ---------------------------------------------------------------------------

class TestDecodeImageValue:
    def _d(self, value):
        from src.api.routes.face_search import _decode_image_value
        return _decode_image_value(value)

    def test_none_returns_none(self):
        assert self._d(None) is None

    def test_empty_returns_none(self):
        assert self._d("") is None

    def test_plain_base64(self):
        encoded = base64.b64encode(b"hello world").decode()
        result = self._d(encoded)
        assert result == b"hello world"

    def test_data_uri_stripped(self):
        encoded = base64.b64encode(b"image data").decode()
        data_uri = f"data:image/jpeg;base64,{encoded}"
        result = self._d(data_uri)
        assert result == b"image data"

    def test_invalid_base64_raises_http_exception(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._d("not-valid-base64!!!")
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# api.routes.graph: _decode_polyline
# ---------------------------------------------------------------------------

class TestDecodePolyline:
    def _p(self, s):
        from src.api.routes.graph import _decode_polyline
        return _decode_polyline(s)

    def test_empty_string_returns_empty(self):
        assert self._p("") == []

    def test_known_encoding(self):
        # Google encoded polyline for (38.5, -120.2) -> "_p~iF~ps|U"
        result = self._p("_p~iF~ps|U")
        assert len(result) == 1
        lat, lng = result[0]
        assert abs(lat - 38.5) < 0.01
        assert abs(lng - (-120.2)) < 0.01

    def test_two_points(self):
        # Encode (0, 0) -> "??"  and two-point sequence
        result = self._p("??")
        assert len(result) == 1

    def test_returns_list_of_lists(self):
        result = self._p("_p~iF~ps|U")
        for point in result:
            assert isinstance(point, list)
            assert len(point) == 2


# ---------------------------------------------------------------------------
# api.routes.graph: _caption_mentions_place
# ---------------------------------------------------------------------------

class TestCaptionMentionsPlace:
    def _c(self, caption, place_name):
        from src.api.routes.graph import _caption_mentions_place
        return _caption_mentions_place(caption, place_name)

    def test_exact_match(self):
        assert self._c("We visited Singapore!", "Singapore") is True

    def test_case_insensitive(self):
        assert self._c("we went to singapore", "Singapore") is True

    def test_no_match(self):
        assert self._c("A nice day in Bangkok", "Singapore") is False

    def test_none_caption_returns_false(self):
        assert self._c(None, "Singapore") is False

    def test_none_place_returns_false(self):
        assert self._c("Nice day", None) is False

    def test_short_place_returns_false(self):
        # Place name < 4 chars → filtered
        assert self._c("Go to LA", "LA") is False

    def test_word_boundary_required(self):
        # "Port" should not match "Portland"
        assert self._c("We went to Portland", "Port") is False

    def test_exact_word_match(self):
        assert self._c("Love Singapore food", "Singapore") is True


# ---------------------------------------------------------------------------
# api.routes.graph: _as_latlng_list
# ---------------------------------------------------------------------------

class TestAsLatlngList:
    def _a(self, raw):
        from src.api.routes.graph import _as_latlng_list
        return _as_latlng_list(raw)

    def test_list_of_lists(self):
        result = self._a([[1.3, 103.8], [1.4, 103.9]])
        assert len(result) == 2
        assert result[0] == [1.3, 103.8]

    def test_json_string(self):
        import json
        raw = json.dumps([[1.3, 103.8]])
        result = self._a(raw)
        assert len(result) == 1

    def test_none_returns_empty(self):
        assert self._a(None) == []

    def test_invalid_string_returns_empty(self):
        assert self._a("not-json") == []

    def test_non_list_returns_empty(self):
        assert self._a({"k": "v"}) == []

    def test_empty_list(self):
        assert self._a([]) == []
