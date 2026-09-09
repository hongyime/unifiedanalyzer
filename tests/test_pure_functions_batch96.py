"""
Pure-function tests — batch 96.

Covers:
- pipeline.media_analysis: _dms_to_decimal, _hamming
- pipeline.face_clustering: _uuid_media_ids, _chunks
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.media_analysis: _dms_to_decimal
# ---------------------------------------------------------------------------

class TestDmsToDecimal:
    def _d(self, dms, ref):
        from src.pipeline.media_analysis import _dms_to_decimal
        return _dms_to_decimal(dms, ref)

    def test_north_positive(self):
        result = self._d((1, 21, 0), "N")
        assert result is not None
        assert result > 0

    def test_south_negative(self):
        result = self._d((1, 21, 0), "S")
        assert result is not None
        assert result < 0

    def test_west_negative(self):
        result = self._d((103, 49, 0), "W")
        assert result is not None
        assert result < 0

    def test_east_positive(self):
        result = self._d((103, 49, 0), "E")
        assert result is not None
        assert result > 0

    def test_none_dms_returns_none(self):
        assert self._d(None, "N") is None

    def test_invalid_dms_returns_none(self):
        assert self._d(("bad", "bad", "bad"), "N") is None

    def test_singapore_lat(self):
        # Singapore ~1.35N
        result = self._d((1, 21, 0), "N")
        assert result is not None
        assert abs(result - 1.35) < 0.1


# ---------------------------------------------------------------------------
# pipeline.media_analysis: _hamming
# ---------------------------------------------------------------------------

class TestHamming:
    def _h(self, h1, h2):
        from src.pipeline.media_analysis import _hamming
        return _hamming(h1, h2)

    def test_identical_hashes_zero_distance(self):
        assert self._h("aabbccdd", "aabbccdd") == 0

    def test_all_ones_vs_all_zeros(self):
        # "ff" = 11111111, "00" = 00000000 → 8 bits difference per byte
        result = self._h("ff", "00")
        assert result == 8

    def test_invalid_hash_returns_999(self):
        assert self._h("notahex", "aabbcc") == 999

    def test_one_bit_difference(self):
        # "01" vs "00" → 1 bit
        assert self._h("01", "00") == 1

    def test_empty_hash_returns_error_code(self):
        # Both empty → int('', 16) raises ValueError → returns 999
        assert self._h("", "") == 999


# ---------------------------------------------------------------------------
# pipeline.face_clustering: _uuid_media_ids
# ---------------------------------------------------------------------------

class TestUuidMediaIds:
    def _u(self, ids):
        from src.pipeline.face_clustering import _uuid_media_ids
        return _uuid_media_ids(ids)

    def test_valid_uuids_preserved(self):
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        result = self._u([uuid_str])
        assert result == [uuid_str]

    def test_invalid_ids_filtered(self):
        result = self._u(["not-a-uuid", "also-not"])
        assert result == []

    def test_mixed_valid_invalid(self):
        valid = "550e8400-e29b-41d4-a716-446655440000"
        result = self._u([valid, "invalid"])
        assert result == [valid]

    def test_empty_list_returns_empty(self):
        assert self._u([]) == []

    def test_normalizes_format(self):
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        result = self._u([uuid_str])
        assert all("-" in v for v in result)


# ---------------------------------------------------------------------------
# pipeline.face_clustering: _chunks
# ---------------------------------------------------------------------------

class TestChunks:
    def _c(self, items, size=5000):
        from src.pipeline.face_clustering import _chunks
        return list(_chunks(items, size))

    def test_empty_list_returns_empty(self):
        assert self._c([]) == []

    def test_single_chunk_when_fewer_than_size(self):
        items = ["a", "b", "c"]
        result = self._c(items, size=10)
        assert result == [["a", "b", "c"]]

    def test_splits_into_multiple_chunks(self):
        items = list(range(25))
        result = self._c(items, size=10)
        assert len(result) == 3
        assert result[0] == list(range(10))
        assert result[1] == list(range(10, 20))
        assert result[2] == list(range(20, 25))

    def test_exact_multiple_of_size(self):
        items = list(range(20))
        result = self._c(items, size=10)
        assert len(result) == 2

    def test_single_item(self):
        result = self._c(["x"], size=5000)
        assert result == [["x"]]
