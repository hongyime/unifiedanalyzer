"""
Pure-function tests — batch 28.

Covers previously untested modules with no DB or I/O:
- api.face_mount: _FACE_ROUTE_MODULES constant, mount_face_api signature
- face.discovery.onedrive: OneDriveHandler.is_onedrive_path
- face.storage.outbox: serialize_embedding, deserialize_embedding
"""
from __future__ import annotations

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# api.face_mount: constants
# ---------------------------------------------------------------------------

class TestFaceMountConstants:
    def test_route_modules_non_empty(self):
        from src.api.face_mount import _FACE_ROUTE_MODULES
        assert len(_FACE_ROUTE_MODULES) > 0

    def test_known_modules_present(self):
        from src.api.face_mount import _FACE_ROUTE_MODULES
        for name in ("stats", "identity", "files", "gallery"):
            assert name in _FACE_ROUTE_MODULES

    def test_mount_face_api_callable(self):
        from src.api.face_mount import mount_face_api
        assert callable(mount_face_api)


# ---------------------------------------------------------------------------
# face.discovery.onedrive: OneDriveHandler.is_onedrive_path
# ---------------------------------------------------------------------------

class TestOneDriveIsPath:
    def _h(self):
        from src.face.discovery.onedrive import OneDriveHandler
        from src.face.config import Settings
        # Use model_construct to avoid .env loading and mkdir side effects
        cfg = Settings.model_construct(
            face_storage_root="/tmp/faces",
            supported_images=".jpg",
            supported_raw=".cr2",
            supported_videos=".mp4",
            onedrive_enabled=False,  # prevents mkdir
            onedrive_download_timeout=30,
            onedrive_max_retries=3,
            onedrive_revert_verify=True,
            onedrive_multi_detect=True,
        )
        return OneDriveHandler(cfg)

    def test_onedrive_path_detected(self):
        h = self._h()
        assert h.is_onedrive_path("/Users/alice/OneDrive/photo.jpg") is True

    def test_skydrive_path_detected(self):
        h = self._h()
        assert h.is_onedrive_path("/skydrive/docs/file.docx") is True

    def test_regular_path_not_onedrive(self):
        h = self._h()
        assert h.is_onedrive_path("/media/photo.jpg") is False

    def test_empty_path_not_onedrive(self):
        h = self._h()
        assert h.is_onedrive_path("") is False

    def test_case_insensitive(self):
        h = self._h()
        assert h.is_onedrive_path("/ONEDRIVE/file.jpg") is True

    def test_windows_backslash_path(self):
        h = self._h()
        assert h.is_onedrive_path("C:\\Users\\alice\\OneDrive\\photo.jpg") is True


# ---------------------------------------------------------------------------
# face.storage.outbox: serialize_embedding, deserialize_embedding
# ---------------------------------------------------------------------------

class TestSerializeEmbedding:
    def test_512d_vector_serializes_to_2048_bytes(self):
        from src.face.storage.outbox import serialize_embedding
        emb = np.zeros(512, dtype=np.float32)
        result = serialize_embedding(emb)
        assert isinstance(result, bytes)
        assert len(result) == 2048  # 512 floats * 4 bytes each

    def test_roundtrip(self):
        from src.face.storage.outbox import serialize_embedding, deserialize_embedding
        emb = np.random.rand(512).astype(np.float32)
        serialized = serialize_embedding(emb)
        restored = deserialize_embedding(serialized)
        np.testing.assert_array_almost_equal(emb, restored)

    def test_wrong_dimension_raises(self):
        from src.face.storage.outbox import serialize_embedding
        with pytest.raises(ValueError):
            serialize_embedding(np.zeros(256, dtype=np.float32))

    def test_unit_norm_preserved(self):
        from src.face.storage.outbox import serialize_embedding, deserialize_embedding
        emb = np.ones(512, dtype=np.float32)
        emb /= np.linalg.norm(emb)
        restored = deserialize_embedding(serialize_embedding(emb))
        assert abs(float(np.linalg.norm(restored)) - 1.0) < 1e-5


class TestDeserializeEmbedding:
    def test_2048_bytes_gives_512d(self):
        from src.face.storage.outbox import deserialize_embedding
        b = np.zeros(512, dtype=np.float32).tobytes()
        result = deserialize_embedding(b)
        assert result.shape == (512,)
        assert result.dtype == np.float32

    def test_wrong_size_raises(self):
        from src.face.storage.outbox import deserialize_embedding
        with pytest.raises(ValueError):
            deserialize_embedding(b"\x00" * 1024)  # 256 floats, not 512

    def test_all_zeros(self):
        from src.face.storage.outbox import deserialize_embedding
        b = np.zeros(512, dtype=np.float32).tobytes()
        result = deserialize_embedding(b)
        assert np.all(result == 0.0)
