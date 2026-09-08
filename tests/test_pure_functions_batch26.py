"""
Pure-function tests — batch 26.

Covers previously untested modules with no DB or I/O:
- face.search.engine: SearchResult dataclass, SearchResponse dataclass
"""
from __future__ import annotations


class TestSearchResult:
    def test_default_optional_fields(self):
        from src.face.search.engine import SearchResult
        r = SearchResult(
            face_id="f1", image_id="i1", file_path="/img.jpg",
            similarity=0.9, quality_score=0.8,
        )
        assert r.thumbnail_path is None
        assert r.bbox_x1 == 0.0
        assert r.bbox_y1 == 0.0
        assert r.bbox_x2 == 0.0
        assert r.bbox_y2 == 0.0

    def test_all_fields_set(self):
        from src.face.search.engine import SearchResult
        r = SearchResult(
            face_id="f1", image_id="i1", file_path="/img.jpg",
            similarity=0.85, quality_score=0.75, thumbnail_path="/thumb.jpg",
            bbox_x1=10.0, bbox_y1=20.0, bbox_x2=100.0, bbox_y2=120.0,
        )
        assert r.similarity == 0.85
        assert r.bbox_x1 == 10.0
        assert r.thumbnail_path == "/thumb.jpg"

    def test_equality_by_value(self):
        from src.face.search.engine import SearchResult
        r1 = SearchResult(face_id="f1", image_id="i1", file_path="/img.jpg",
                          similarity=0.9, quality_score=0.8)
        r2 = SearchResult(face_id="f1", image_id="i1", file_path="/img.jpg",
                          similarity=0.9, quality_score=0.8)
        assert r1 == r2

    def test_face_id_stored(self):
        from src.face.search.engine import SearchResult
        r = SearchResult(face_id="abc123", image_id="img1",
                         file_path="/photo.jpg", similarity=0.7, quality_score=0.6)
        assert r.face_id == "abc123"

    def test_image_id_stored(self):
        from src.face.search.engine import SearchResult
        r = SearchResult(face_id="f1", image_id="img42",
                         file_path="/photo.jpg", similarity=0.7, quality_score=0.6)
        assert r.image_id == "img42"

    def test_file_path_stored(self):
        from src.face.search.engine import SearchResult
        r = SearchResult(face_id="f1", image_id="i1",
                         file_path="/some/path/photo.jpg",
                         similarity=0.7, quality_score=0.6)
        assert r.file_path == "/some/path/photo.jpg"


class TestSearchResponse:
    def test_fields(self):
        from src.face.search.engine import SearchResult, SearchResponse
        r = SearchResult(face_id="f1", image_id="i1", file_path="/img.jpg",
                         similarity=0.9, quality_score=0.8)
        resp = SearchResponse(results=[r], total_found=1,
                              query_embedding_dim=512, search_time_ms=12.5)
        assert resp.total_found == 1
        assert resp.query_embedding_dim == 512
        assert abs(resp.search_time_ms - 12.5) < 1e-9
        assert len(resp.results) == 1

    def test_empty_results(self):
        from src.face.search.engine import SearchResponse
        resp = SearchResponse(results=[], total_found=0,
                              query_embedding_dim=512, search_time_ms=0.0)
        assert resp.results == []
        assert resp.total_found == 0

    def test_dim_384(self):
        from src.face.search.engine import SearchResponse
        resp = SearchResponse(results=[], total_found=0,
                              query_embedding_dim=384, search_time_ms=5.0)
        assert resp.query_embedding_dim == 384

    def test_search_time_zero(self):
        from src.face.search.engine import SearchResponse
        resp = SearchResponse(results=[], total_found=0,
                              query_embedding_dim=512, search_time_ms=0.0)
        assert resp.search_time_ms == 0.0
