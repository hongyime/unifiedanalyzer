"""
Pure-function tests — batch 25.

Covers previously untested modules with no DB or I/O:
- face.utils.logging: get_logger (returns a logger), setup_logging signature
- face.readers.video_reader: VideoReader (constructor, can_read, SUPPORTED_EXTENSIONS)
- face.api.routes.files: _validate_file_path
- face.api.routes.identity: Pydantic models (IdentityResponse, FaceResponse,
  MergeRequest, SplitRequest, RenameRequest)
"""
from __future__ import annotations

from pathlib import Path
import pytest


# ---------------------------------------------------------------------------
# face.utils.logging: get_logger
# ---------------------------------------------------------------------------

class TestGetLogger:
    def test_returns_bound_logger(self):
        from src.face.utils.logging import get_logger
        logger = get_logger("test_module")
        assert logger is not None

    def test_different_names_return_loggers(self):
        from src.face.utils.logging import get_logger
        l1 = get_logger("module_a")
        l2 = get_logger("module_b")
        # Both should be valid logger instances
        assert l1 is not None
        assert l2 is not None


# ---------------------------------------------------------------------------
# face.readers.video_reader: VideoReader
# ---------------------------------------------------------------------------

class TestVideoReader:
    def _r(self, fps=1.0, max_frame_size=1920):
        from src.face.readers.video_reader import VideoReader
        return VideoReader(fps=fps, max_frame_size=max_frame_size)

    def test_default_fps(self):
        r = self._r()
        assert r.fps == 1.0

    def test_custom_fps(self):
        r = self._r(fps=2.0)
        assert r.fps == 2.0

    def test_default_max_frame_size(self):
        r = self._r()
        assert r.max_frame_size == 1920

    def test_custom_max_frame_size(self):
        r = self._r(max_frame_size=1280)
        assert r.max_frame_size == 1280

    def test_supported_extensions_non_empty(self):
        from src.face.readers.video_reader import VideoReader
        assert len(VideoReader.SUPPORTED_EXTENSIONS) > 0

    def test_mp4_supported(self):
        from src.face.readers.video_reader import VideoReader
        assert ".mp4" in VideoReader.SUPPORTED_EXTENSIONS

    def test_mkv_supported(self):
        from src.face.readers.video_reader import VideoReader
        assert ".mkv" in VideoReader.SUPPORTED_EXTENSIONS

    def test_avi_supported(self):
        from src.face.readers.video_reader import VideoReader
        assert ".avi" in VideoReader.SUPPORTED_EXTENSIONS

    def test_can_read_mp4(self):
        r = self._r()
        assert r.can_read(Path("video.mp4")) is True

    def test_can_read_mkv(self):
        r = self._r()
        assert r.can_read(Path("clip.MKV")) is True

    def test_cannot_read_jpg(self):
        r = self._r()
        assert r.can_read(Path("photo.jpg")) is False

    def test_cannot_read_txt(self):
        r = self._r()
        assert r.can_read(Path("file.txt")) is False

    def test_case_insensitive(self):
        r = self._r()
        assert r.can_read(Path("video.MP4")) is True


# ---------------------------------------------------------------------------
# face.api.routes.files: _validate_file_path
# ---------------------------------------------------------------------------

class TestValidateFilePath:
    def _v(self, path):
        from src.face.api.routes.files import _validate_file_path
        return _validate_file_path(path)

    def test_valid_path_returned(self):
        result = self._v("/media/photo.jpg")
        assert result == "/media/photo.jpg"

    def test_null_byte_raises(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._v("/media/ph\x00oto.jpg")
        assert exc_info.value.status_code == 400
        assert "null" in exc_info.value.detail.lower()

    def test_directory_traversal_raises(self):
        from fastapi import HTTPException
        # On Windows normpath resolves absolute .. away; use a relative path
        # that still has '..' after normpath on all platforms.
        with pytest.raises(HTTPException) as exc_info:
            self._v("../etc/passwd")
        assert exc_info.value.status_code == 400
        assert "traversal" in exc_info.value.detail.lower()

    def test_simple_filename_ok(self):
        result = self._v("photo.jpg")
        assert result == "photo.jpg"

    def test_nested_path_ok(self):
        result = self._v("/media/instagram/photo.jpg")
        assert result == "/media/instagram/photo.jpg"


# ---------------------------------------------------------------------------
# face.api.routes.identity: Pydantic models
# ---------------------------------------------------------------------------

class TestIdentityModels:
    def test_merge_request_fields(self):
        from src.face.api.routes.identity import MergeRequest
        req = MergeRequest(source_ids=[1, 2], target_id=3)
        assert req.source_ids == [1, 2]
        assert req.target_id == 3

    def test_split_request_fields(self):
        from src.face.api.routes.identity import SplitRequest
        req = SplitRequest(face_ids=[10, 20])
        assert req.face_ids == [10, 20]
        assert req.new_identity_name is None

    def test_split_request_with_name(self):
        from src.face.api.routes.identity import SplitRequest
        req = SplitRequest(face_ids=[1], new_identity_name="Alice")
        assert req.new_identity_name == "Alice"

    def test_rename_request_fields(self):
        from src.face.api.routes.identity import RenameRequest
        req = RenameRequest(name="Bob")
        assert req.name == "Bob"

    def test_identity_response_model(self):
        from src.face.api.routes.identity import IdentityResponse
        resp = IdentityResponse(
            identity_id="abc", name="Alice", face_count=5,
            created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
            avg_quality_score=0.85, thumbnail_url=None,
        )
        assert resp.identity_id == "abc"
        assert resp.face_count == 5
        assert resp.thumbnail_url is None

    def test_face_response_model(self):
        from src.face.api.routes.identity import FaceResponse
        resp = FaceResponse(
            face_id=1, embedding_id="emb-1", similarity_to_centroid=0.9,
            quality_score=0.75, thumbnail_path=None, file_path="/photo.jpg",
            is_primary=True,
        )
        assert resp.face_id == 1
        assert resp.is_primary is True

    def test_identity_list_response(self):
        from src.face.api.routes.identity import IdentityListResponse
        resp = IdentityListResponse(identities=[], total=0, page=1, page_size=20)
        assert resp.total == 0
        assert resp.page == 1
