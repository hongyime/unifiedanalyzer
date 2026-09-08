"""
Pure-function tests — batch 22.

Covers previously untested modules with no DB or I/O:
- api.routes.face_search: _iso, _vector_literal
- api.routes.export: _hash_value
- face.search.multi_face: MultiFaceQuery dataclass, MultiFaceResult dataclass,
  MultiFaceSearcher.create_query_from_detections
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# api.routes.face_search: _iso, _vector_literal
# ---------------------------------------------------------------------------

class TestFaceSearchIso:
    def _i(self, v):
        from src.api.routes.face_search import _iso
        return _iso(v)

    def test_datetime_isoformat(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        assert "2026-01-15" in self._i(dt)

    def test_none_returns_none(self):
        assert self._i(None) is None

    def test_falsy_zero_returns_none(self):
        assert self._i(0) is None


class TestVectorLiteral:
    def _v(self, vector):
        from src.api.routes.face_search import _vector_literal
        return _vector_literal(vector)

    def test_basic_vector(self):
        result = self._v([1.0, 0.0, -1.0])
        assert result.startswith("[")
        assert result.endswith("]")
        assert "1.0000000" in result

    def test_empty_vector(self):
        result = self._v([])
        assert result == "[]"

    def test_precision_7_decimal(self):
        result = self._v([0.123456789])
        # Should have 7 decimal places
        assert "0.1234568" in result or "0.123456" in result

    def test_comma_separated(self):
        result = self._v([1.0, 2.0, 3.0])
        parts = result.strip("[]").split(",")
        assert len(parts) == 3


# ---------------------------------------------------------------------------
# api.routes.export: _hash_value
# ---------------------------------------------------------------------------

class TestHashValue:
    def _h(self, v):
        from src.api.routes.export import _hash_value
        return _hash_value(v)

    def test_returns_16_chars(self):
        result = self._h("test@example.com")
        assert len(result) == 16

    def test_none_returns_none(self):
        assert self._h(None) is None

    def test_empty_returns_none(self):
        assert self._h("") is None

    def test_deterministic(self):
        assert self._h("foo") == self._h("foo")

    def test_different_inputs_differ(self):
        assert self._h("a") != self._h("b")

    def test_hex_only(self):
        result = self._h("test")
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# face.search.multi_face: MultiFaceQuery, MultiFaceResult, create_query_from_detections
# ---------------------------------------------------------------------------

class TestMultiFaceQuery:
    def test_default_empty_lists(self):
        import numpy as np
        from src.face.search.multi_face import MultiFaceQuery
        q = MultiFaceQuery(embeddings=[np.zeros(128)], mode="any")
        assert q.face_bboxes == []
        assert q.face_quality_scores == []

    def test_mode_stored(self):
        import numpy as np
        from src.face.search.multi_face import MultiFaceQuery
        q = MultiFaceQuery(embeddings=[np.zeros(128)], mode="all")
        assert q.mode == "all"

    def test_embeddings_length(self):
        import numpy as np
        from src.face.search.multi_face import MultiFaceQuery
        embs = [np.zeros(128), np.ones(128)]
        q = MultiFaceQuery(embeddings=embs, mode="any")
        assert len(q.embeddings) == 2


class TestMultiFaceResult:
    def test_dataclass_fields(self):
        from src.face.search.multi_face import MultiFaceResult
        result = MultiFaceResult(
            mode="any",
            faces_detected=2,
            results=[],
            per_face_results={},
        )
        assert result.mode == "any"
        assert result.faces_detected == 2
        assert result.results == []
        assert result.common_faces is None

    def test_common_faces_set(self):
        from src.face.search.multi_face import MultiFaceResult
        result = MultiFaceResult(
            mode="all",
            faces_detected=1,
            results=[],
            per_face_results={},
            common_faces=[],
        )
        assert result.common_faces == []


class TestCreateQueryFromDetections:
    def _make_searcher(self):
        from src.face.search.multi_face import MultiFaceSearcher
        # Pass a dummy engine — create_query_from_detections doesn't use it
        return MultiFaceSearcher(search_engine=None)

    def test_basic_any_mode(self):
        import numpy as np
        searcher = self._make_searcher()
        embs = [np.zeros(128)]
        q = searcher.create_query_from_detections(embs, mode="any")
        assert q.mode == "any"
        assert len(q.embeddings) == 1

    def test_all_mode(self):
        import numpy as np
        searcher = self._make_searcher()
        embs = [np.zeros(128), np.ones(128)]
        q = searcher.create_query_from_detections(embs, mode="all")
        assert q.mode == "all"
        assert len(q.embeddings) == 2

    def test_bboxes_passed_through(self):
        import numpy as np
        searcher = self._make_searcher()
        bboxes = [(0, 0, 100, 100)]
        q = searcher.create_query_from_detections([np.zeros(128)], bboxes=bboxes)
        assert q.face_bboxes == bboxes

    def test_quality_scores_passed(self):
        import numpy as np
        searcher = self._make_searcher()
        q = searcher.create_query_from_detections(
            [np.zeros(128)], quality_scores=[0.9]
        )
        assert q.face_quality_scores == [0.9]

    def test_none_bboxes_defaults_empty(self):
        import numpy as np
        searcher = self._make_searcher()
        q = searcher.create_query_from_detections([np.zeros(128)])
        assert q.face_bboxes == []

    def test_none_quality_defaults_empty(self):
        import numpy as np
        searcher = self._make_searcher()
        q = searcher.create_query_from_detections([np.zeros(128)])
        assert q.face_quality_scores == []
