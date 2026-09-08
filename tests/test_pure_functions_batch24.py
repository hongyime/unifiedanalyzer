"""
Pure-function tests — batch 24.

Covers previously untested modules with no DB or I/O:
- api.face_lookup: face_crop_url
- face.api.routes.gallery: _crop_margin/_crop_max constants
- face.readers.image_reader: ImageReader.can_read, SUPPORTED_EXTENSIONS,
  ImageReader constructor
- face.readers.raw_heic: RawHEICReader.can_read, SUPPORTED_EXTENSIONS
"""
from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# api.face_lookup: face_crop_url
# ---------------------------------------------------------------------------

class TestFaceCropUrl:
    def _u(self, face_id):
        from src.api.face_lookup import face_crop_url
        return face_crop_url(face_id)

    def test_returns_url_for_face_id(self):
        result = self._u(42)
        assert result is not None
        assert "42" in result
        assert result.startswith("/")

    def test_none_returns_none(self):
        assert self._u(None) is None

    def test_zero_not_none(self):
        # face_id=0 is a valid int, not None
        result = self._u(0)
        assert result is not None
        assert "0" in result

    def test_url_contains_gallery_path(self):
        result = self._u(123)
        assert "gallery" in result or "face" in result

    def test_url_contains_crop(self):
        result = self._u(99)
        assert "crop" in result


# ---------------------------------------------------------------------------
# face.api.routes.gallery: constants
# ---------------------------------------------------------------------------

class TestGalleryConstants:
    def test_crop_margin_positive(self):
        from src.face.api.routes.gallery import _CROP_MARGIN
        assert _CROP_MARGIN > 0

    def test_crop_margin_reasonable(self):
        from src.face.api.routes.gallery import _CROP_MARGIN
        # Should be a fraction, not > 1
        assert _CROP_MARGIN < 1.0

    def test_crop_max_positive(self):
        from src.face.api.routes.gallery import _CROP_MAX
        assert _CROP_MAX > 0

    def test_crop_max_reasonable(self):
        from src.face.api.routes.gallery import _CROP_MAX
        # Should be a pixel value, not absurdly large
        assert _CROP_MAX <= 2048


# ---------------------------------------------------------------------------
# face.readers.image_reader: ImageReader
# ---------------------------------------------------------------------------

class TestImageReader:
    def _r(self, max_size=4096):
        from src.face.readers.image_reader import ImageReader
        return ImageReader(max_size=max_size)

    def test_default_max_size(self):
        r = self._r()
        assert r.max_size == 4096

    def test_custom_max_size(self):
        r = self._r(max_size=1024)
        assert r.max_size == 1024

    def test_supported_extensions_non_empty(self):
        from src.face.readers.image_reader import ImageReader
        assert len(ImageReader.SUPPORTED_EXTENSIONS) > 0

    def test_jpg_supported(self):
        from src.face.readers.image_reader import ImageReader
        assert ".jpg" in ImageReader.SUPPORTED_EXTENSIONS

    def test_png_supported(self):
        from src.face.readers.image_reader import ImageReader
        assert ".png" in ImageReader.SUPPORTED_EXTENSIONS

    def test_heic_supported(self):
        from src.face.readers.image_reader import ImageReader
        assert ".heic" in ImageReader.SUPPORTED_EXTENSIONS

    def test_can_read_jpg(self):
        r = self._r()
        assert r.can_read(Path("photo.jpg")) is True

    def test_can_read_png(self):
        r = self._r()
        assert r.can_read(Path("image.PNG")) is True

    def test_cannot_read_mp4(self):
        r = self._r()
        assert r.can_read(Path("video.mp4")) is False

    def test_cannot_read_txt(self):
        r = self._r()
        assert r.can_read(Path("file.txt")) is False

    def test_case_insensitive(self):
        r = self._r()
        assert r.can_read(Path("photo.JPEG")) is True


# ---------------------------------------------------------------------------
# face.readers.raw_heic: RawHEICReader
# ---------------------------------------------------------------------------

class TestRawHeicReader:
    def _r(self, max_size=4096):
        from src.face.readers.raw_heic import RawHEICReader
        return RawHEICReader(max_size=max_size)

    def test_default_max_size(self):
        r = self._r()
        assert r.max_size == 4096

    def test_custom_max_size(self):
        r = self._r(max_size=512)
        assert r.max_size == 512

    def test_supported_extensions_non_empty(self):
        from src.face.readers.raw_heic import RawHEICReader
        assert len(RawHEICReader.SUPPORTED_EXTENSIONS) > 0

    def test_heic_supported(self):
        from src.face.readers.raw_heic import RawHEICReader
        assert ".heic" in RawHEICReader.SUPPORTED_EXTENSIONS

    def test_dng_supported(self):
        from src.face.readers.raw_heic import RawHEICReader
        assert ".dng" in RawHEICReader.SUPPORTED_EXTENSIONS

    def test_cr2_supported(self):
        from src.face.readers.raw_heic import RawHEICReader
        assert ".cr2" in RawHEICReader.SUPPORTED_EXTENSIONS

    def test_can_read_heic(self):
        r = self._r()
        assert r.can_read(Path("photo.heic")) is True

    def test_can_read_dng(self):
        r = self._r()
        assert r.can_read(Path("RAW.DNG")) is True

    def test_cannot_read_jpg(self):
        r = self._r()
        assert r.can_read(Path("photo.jpg")) is False

    def test_cannot_read_mp4(self):
        r = self._r()
        assert r.can_read(Path("video.mp4")) is False

    def test_case_insensitive(self):
        r = self._r()
        assert r.can_read(Path("photo.HEIC")) is True
