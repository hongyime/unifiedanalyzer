"""
QA-lane tests for remaining pure functions in src/api/routes/face_search.py:
- _decode_image_value: base64 decoding, data-URI stripping, raises on invalid
- _infer_platform: filename/path → platform string inference
- _media_for_row: context lookup and fallback chain
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from src.api.routes.face_search import (
    _decode_image_value,
    _infer_platform,
    _media_for_row,
)

_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _decode_image_value
# ---------------------------------------------------------------------------

class TestDecodeImageValue:
    def test_none_returns_none(self):
        assert _decode_image_value(None) is None

    def test_empty_returns_none(self):
        assert _decode_image_value("") is None

    def test_valid_base64_decoded(self):
        raw = base64.b64encode(b"hello").decode()
        result = _decode_image_value(raw)
        assert result == b"hello"

    def test_data_uri_stripped(self):
        raw = base64.b64encode(b"hello").decode()
        uri = f"data:image/jpeg;base64,{raw}"
        result = _decode_image_value(uri)
        assert result == b"hello"

    def test_invalid_base64_raises_400(self):
        with pytest.raises(HTTPException) as exc:
            _decode_image_value("not-valid-base64!!!")
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# _infer_platform
# ---------------------------------------------------------------------------

class TestInferPlatform:
    def _row(self, file_path="", media_item_id=""):
        return {"file_path": file_path, "media_item_id": media_item_id}

    def test_instagram_in_path(self):
        row = self._row(file_path="/media/instagram/photo.jpg")
        assert _infer_platform(row) == "instagram"

    def test_telegram_in_path(self):
        row = self._row(file_path="/media/telegram/video.mp4")
        assert _infer_platform(row) == "telegram"

    def test_strava_in_path(self):
        row = self._row(file_path="/media/strava/activity.jpg")
        assert _infer_platform(row) == "strava"

    def test_unknown_returns_none(self):
        row = self._row(file_path="/media/unknown/file.jpg")
        assert _infer_platform(row) is None

    def test_platform_in_media_item_id(self):
        row = self._row(media_item_id="instagram-photo-123")
        assert _infer_platform(row) == "instagram"

    def test_none_row_values_return_none(self):
        row = {"file_path": None, "media_item_id": None}
        assert _infer_platform(row) is None


# ---------------------------------------------------------------------------
# _media_for_row
# ---------------------------------------------------------------------------

class TestMediaForRow:
    def _row(self, **overrides):
        base = {
            "media_item_id": "mid-1",
            "file_hash": "hash-1",
            "file_path": "/media/instagram/photo.jpg",
            "image_created_at": None,
        }
        base.update(overrides)
        return base

    def test_context_from_id_lookup(self):
        context = {
            "media_item_id": "mid-1",
            "source": "instagram",
            "content_type": "image/jpeg",
            "content_id": "cid-1",
            "filename": "photo.jpg",
            "file_path": "/media/instagram/photo.jpg",
            "source_url": "https://example.com/photo",
            "collected_at": _NOW,
        }
        result = _media_for_row(self._row(), by_id={"mid-1": context}, by_hash={}, by_path={})
        assert result["platform"] == "instagram"
        assert result["content_type"] == "image/jpeg"

    def test_fallback_to_hash_lookup(self):
        context = {"source": "telegram", "content_type": None, "content_id": None,
                   "filename": None, "file_path": None, "source_url": None, "collected_at": None,
                   "media_item_id": "mid-1"}
        row = self._row(media_item_id=None)
        result = _media_for_row(row, by_id={}, by_hash={"hash-1": context}, by_path={})
        assert result["platform"] == "telegram"

    def test_fallback_to_path_lookup(self):
        context = {"source": "github", "content_type": None, "content_id": None,
                   "filename": None, "file_path": "/media/instagram/photo.jpg",
                   "source_url": None, "collected_at": None, "media_item_id": None}
        row = self._row(media_item_id=None, file_hash=None)
        result = _media_for_row(row, by_id={}, by_hash={}, by_path={"/media/instagram/photo.jpg": context})
        assert result["platform"] == "github"

    def test_infer_platform_when_no_context(self):
        row = self._row(media_item_id=None, file_hash=None)
        result = _media_for_row(row, by_id={}, by_hash={}, by_path={})
        # File path contains "instagram" → infer platform
        assert result["platform"] == "instagram"

    def test_filename_from_file_path(self):
        row = self._row(media_item_id=None, file_hash=None)
        result = _media_for_row(row, by_id={}, by_hash={}, by_path={})
        assert result["filename"] == "photo.jpg"
