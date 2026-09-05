"""
QA-lane tests for pure functions in src/pipeline/media_analysis.py.

Covers:
- _dms_to_decimal: DMS tuple → decimal degrees, N/S/E/W refs
- _extract_exif_device: serial-gated device fingerprint extraction
- _hamming: hex perceptual hash Hamming distance
"""
from __future__ import annotations

import pytest

from src.pipeline.media_analysis import (
    _dms_to_decimal,
    _extract_exif_device,
    _hamming,
)


# ---------------------------------------------------------------------------
# _dms_to_decimal
# ---------------------------------------------------------------------------

class TestDmsToDecimal:
    def test_north_positive(self):
        # 1°17'N → 1 + 17/60 ≈ 1.2833
        result = _dms_to_decimal((1, 17, 0), "N")
        assert result is not None
        assert abs(result - (1 + 17/60)) < 0.001

    def test_south_negative(self):
        result = _dms_to_decimal((33, 52, 0), "S")
        assert result is not None
        assert result < 0

    def test_east_positive(self):
        result = _dms_to_decimal((103, 49, 0), "E")
        assert result is not None
        assert result > 0

    def test_west_negative(self):
        result = _dms_to_decimal((74, 0, 0), "W")
        assert result is not None
        assert result < 0

    def test_zero_degrees(self):
        result = _dms_to_decimal((0, 0, 0), "N")
        assert result == 0.0

    def test_seconds_included(self):
        # 1°0'36" = 1 + 36/3600 = 1.01
        result = _dms_to_decimal((1, 0, 36), "N")
        assert result is not None
        assert abs(result - 1.01) < 0.001

    def test_invalid_tuple_returns_none(self):
        assert _dms_to_decimal(("bad", "data", "here"), "N") is None

    def test_none_input_returns_none(self):
        assert _dms_to_decimal(None, "N") is None

    def test_too_short_tuple_returns_none(self):
        assert _dms_to_decimal((1, 2), "N") is None


# ---------------------------------------------------------------------------
# _extract_exif_device
# ---------------------------------------------------------------------------

class TestExtractExifDevice:
    def test_returns_none_when_no_serial(self):
        exif = {271: "Apple", 272: "iPhone 13"}
        exif_ifd = {}
        assert _extract_exif_device(exif, exif_ifd) is None

    def test_returns_dict_when_body_serial_present(self):
        exif = {271: "Canon", 272: "EOS R5"}
        exif_ifd = {42033: "ABC123", 42036: None, 42037: None}
        result = _extract_exif_device(exif, exif_ifd)
        assert result is not None
        assert result["body_serial"] == "ABC123"

    def test_returns_dict_when_only_lens_serial_present(self):
        exif = {}
        exif_ifd = {42037: "LENS456", 42033: None}
        result = _extract_exif_device(exif, exif_ifd)
        assert result is not None
        assert result["lens_serial"] == "LENS456"

    def test_make_and_model_included(self):
        exif = {271: "Nikon", 272: "Z9"}
        exif_ifd = {42033: "SN999"}
        result = _extract_exif_device(exif, exif_ifd)
        assert result is not None
        assert result["make"] == "Nikon"
        assert result["model"] == "Z9"

    def test_empty_serial_string_treated_as_none(self):
        exif = {271: "Sony"}
        exif_ifd = {42033: "   ", 42037: ""}
        assert _extract_exif_device(exif, exif_ifd) is None

    def test_empty_exif_dicts_return_none(self):
        assert _extract_exif_device({}, {}) is None


# ---------------------------------------------------------------------------
# _hamming
# ---------------------------------------------------------------------------

class TestHamming:
    def test_identical_hashes_give_0(self):
        h = "aabbccdd"
        assert _hamming(h, h) == 0

    def test_single_bit_difference(self):
        # aabb vs aab9 — last nibble differs: 0xd vs 0x9 = 4 = 0b0100 → 1 bit
        assert _hamming("aabbccdd", "aabbcc9d") > 0

    def test_completely_different_hashes(self):
        assert _hamming("0000000000000000", "ffffffffffffffff") == 64

    def test_invalid_hex_returns_999(self):
        assert _hamming("nothex", "aabbccdd") == 999

    def test_empty_string_returns_999(self):
        assert _hamming("", "aabbccdd") == 999

    def test_symmetry(self):
        h1, h2 = "aabbccdd", "11223344"
        assert _hamming(h1, h2) == _hamming(h2, h1)

    def test_known_hamming_distance(self):
        # 0x0 vs 0xf = 1111 in binary → 4 bits differ
        assert _hamming("0", "f") == 4
