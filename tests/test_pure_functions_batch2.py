"""
Pure-function tests — batch 2.

Covers previously untested modules with no DB or I/O dependency:
- src.face.utils.hashing: compute_content_hash
- src.face.config: Settings properties (image_extensions, raw_extensions,
  video_extensions, all_supported_extensions, database_url, thumbnail_cache_path,
  faiss_live_path, faiss_staging_dir)
- src.face.search.ranking: RankingStrategy.rank(), rank_by_similarity_only()
- src.pipeline.graph_analytics: _decode_meta
- src.api.routes.data_quality: _cache_ttl_seconds, _parse_ts, _cache_age_seconds
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone, timedelta

import pytest

# ---------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------

class TestComputeContentHash:
    def test_known_value(self):
        from src.face.utils.hashing import compute_content_hash
        data = b"hello world"
        expected = hashlib.sha256(data).hexdigest()
        assert compute_content_hash(data) == expected

    def test_empty_bytes(self):
        from src.face.utils.hashing import compute_content_hash
        result = compute_content_hash(b"")
        assert result == hashlib.sha256(b"").hexdigest()
        assert len(result) == 64

    def test_deterministic(self):
        from src.face.utils.hashing import compute_content_hash
        data = b"test content"
        assert compute_content_hash(data) == compute_content_hash(data)

    def test_different_inputs_different_hashes(self):
        from src.face.utils.hashing import compute_content_hash
        assert compute_content_hash(b"a") != compute_content_hash(b"b")

    def test_returns_hex_string(self):
        from src.face.utils.hashing import compute_content_hash
        result = compute_content_hash(b"test")
        assert isinstance(result, str)
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# face/config — Settings properties (no env side-effects; instantiate fresh)
# ---------------------------------------------------------------------------

class TestSettingsProperties:
    def _make_settings(self, **overrides):
        from src.face.config import Settings
        # env_file=None prevents loading .env from disk
        return Settings.model_construct(**{
            "supported_images": ".jpg,.jpeg,.png,.webp,.gif,.bmp,.heic,.heif,.tiff,.tif",
            "supported_raw": ".cr2,.cr3,.nef,.arw,.orf,.rw2,.dng,.raf",
            "supported_videos": ".mp4,.mov,.m4v,.avi,.mkv,.wmv,.webm,.flv,.3gp",
            "face_storage_root": "Z:/faces",
            "postgres_host": "localhost",
            "postgres_port": 5432,
            "postgres_db": "facetracker",
            "postgres_user": "postgres",
            "postgres_password": "changeme",
            "api_token": "",
            **overrides,
        })

    def test_image_extensions_list(self):
        s = self._make_settings()
        exts = s.image_extensions
        assert isinstance(exts, list)
        assert ".jpg" in exts
        assert ".heic" in exts

    def test_raw_extensions_list(self):
        s = self._make_settings()
        exts = s.raw_extensions
        assert ".cr2" in exts
        assert ".dng" in exts

    def test_video_extensions_list(self):
        s = self._make_settings()
        exts = s.video_extensions
        assert ".mp4" in exts
        assert ".mkv" in exts

    def test_all_supported_extensions_union(self):
        s = self._make_settings()
        all_exts = s.all_supported_extensions
        assert ".jpg" in all_exts
        assert ".cr2" in all_exts
        assert ".mp4" in all_exts
        assert len(all_exts) == len(s.image_extensions) + len(s.raw_extensions) + len(s.video_extensions)

    def test_thumbnail_cache_path(self):
        s = self._make_settings(face_storage_root="Z:/faces")
        assert s.thumbnail_cache_path == "Z:/faces/media/thumbnails"

    def test_faiss_live_path(self):
        s = self._make_settings(face_storage_root="Z:/faces")
        assert s.faiss_live_path == "Z:/faces/embeddings/live/face_index.faiss"

    def test_faiss_staging_dir(self):
        s = self._make_settings(face_storage_root="Z:/faces")
        assert s.faiss_staging_dir == "Z:/faces/embeddings/staging"

    def test_database_url_fallback(self, monkeypatch):
        monkeypatch.delenv("ANALYZER_DATABASE_URL", raising=False)
        s = self._make_settings()
        url = s.database_url
        assert "localhost" in url
        assert "facetracker" in url

    def test_database_url_from_env(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATABASE_URL", "postgresql+asyncpg://collector:collector@localhost:5432/unifiedanalyzer")
        s = self._make_settings()
        url = s.database_url
        assert "psycopg2" in url
        assert "unifiedanalyzer" in url


# ---------------------------------------------------------------------------
# face/search/ranking — pure math, no DB
# ---------------------------------------------------------------------------

def _make_result(face_id=1, image_id=1, similarity=0.9, quality_score=0.8):
    from src.face.search.engine import SearchResult
    return SearchResult(
        face_id=face_id,
        image_id=image_id,
        file_path="/tmp/img.jpg",
        similarity=similarity,
        quality_score=quality_score,
        thumbnail_path=None,
        bbox_x1=0, bbox_y1=0, bbox_x2=100, bbox_y2=100,
    )


class TestRankingStrategy:
    def test_rank_returns_sorted_descending(self):
        from src.face.search.ranking import RankingStrategy
        rs = RankingStrategy()
        results = [
            _make_result(face_id=1, similarity=0.5, quality_score=0.5),
            _make_result(face_id=2, similarity=0.9, quality_score=0.9),
            _make_result(face_id=3, similarity=0.7, quality_score=0.7),
        ]
        ranked = rs.rank(results)
        scores = [r.ranking_score for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_rank_empty_list(self):
        from src.face.search.ranking import RankingStrategy
        rs = RankingStrategy()
        assert rs.rank([]) == []

    def test_rank_weights_normalize(self):
        from src.face.search.ranking import RankingStrategy
        rs = RankingStrategy(similarity_weight=1.0, quality_weight=1.0, recency_weight=1.0)
        # weights should sum to 1.0 after normalization
        total = rs.similarity_weight + rs.quality_weight + rs.recency_weight
        assert abs(total - 1.0) < 1e-9

    def test_rank_by_similarity_only_sorted(self):
        from src.face.search.ranking import RankingStrategy
        rs = RankingStrategy()
        results = [
            _make_result(face_id=1, similarity=0.3),
            _make_result(face_id=2, similarity=0.95),
            _make_result(face_id=3, similarity=0.6),
        ]
        ranked = rs.rank_by_similarity_only(results)
        assert ranked[0].face_id == 2
        assert ranked[-1].face_id == 1

    def test_rank_by_similarity_only_score_equals_similarity(self):
        from src.face.search.ranking import RankingStrategy
        rs = RankingStrategy()
        result = _make_result(similarity=0.77)
        ranked = rs.rank_by_similarity_only([result])
        assert ranked[0].ranking_score == pytest.approx(0.77)

    def test_rank_quality_clamped_to_1(self):
        """quality_score > 1.0 should be clamped to 1.0."""
        from src.face.search.ranking import RankingStrategy
        rs = RankingStrategy(similarity_weight=0.0, quality_weight=1.0, recency_weight=0.0)
        result = _make_result(similarity=0.0, quality_score=999.0)
        ranked = rs.rank([result])
        # quality_factor clamped to 1.0, weight is full → score ≈ 1.0
        assert ranked[0].ranking_score <= 1.0 + 1e-9

    def test_ranked_result_has_ranking_score_field(self):
        from src.face.search.ranking import RankingStrategy, RankedResult
        rs = RankingStrategy()
        ranked = rs.rank([_make_result()])
        assert isinstance(ranked[0], RankedResult)
        assert hasattr(ranked[0], "ranking_score")


# ---------------------------------------------------------------------------
# graph_analytics._decode_meta
# ---------------------------------------------------------------------------

class TestDecodeMetaGraphAnalytics:
    def _decode_meta(self, raw):
        from src.pipeline.graph_analytics import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        data = {"key": "value"}
        assert self._decode_meta(data) == data

    def test_json_string(self):
        assert self._decode_meta('{"a": 1}') == {"a": 1}

    def test_json_bytes(self):
        assert self._decode_meta(b'{"b": 2}') == {"b": 2}

    def test_invalid_json_string_returns_empty(self):
        assert self._decode_meta("not-json") == {}

    def test_json_array_returns_empty(self):
        # JSON array is not a dict → return {}
        assert self._decode_meta("[1, 2, 3]") == {}

    def test_none_returns_empty(self):
        assert self._decode_meta(None) == {}

    def test_int_returns_empty(self):
        assert self._decode_meta(42) == {}

    def test_empty_string_returns_empty(self):
        assert self._decode_meta("") == {}


# ---------------------------------------------------------------------------
# data_quality route pure helpers
# ---------------------------------------------------------------------------

class TestDataQualityPureHelpers:
    def test_cache_ttl_seconds_default(self, monkeypatch):
        monkeypatch.delenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", raising=False)
        from importlib import reload
        import src.api.routes.data_quality as dq
        reload(dq)
        assert dq._cache_ttl_seconds() == 900

    def test_cache_ttl_seconds_custom(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", "300")
        from src.api.routes.data_quality import _cache_ttl_seconds
        assert _cache_ttl_seconds() == 300

    def test_cache_ttl_seconds_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", "not-a-number")
        from src.api.routes.data_quality import _cache_ttl_seconds
        assert _cache_ttl_seconds() == 900

    def test_parse_ts_valid_iso(self):
        from src.api.routes.data_quality import _parse_ts
        result = _parse_ts("2026-01-15T12:00:00+00:00")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_parse_ts_z_suffix(self):
        from src.api.routes.data_quality import _parse_ts
        result = _parse_ts("2026-01-15T12:00:00Z")
        assert isinstance(result, datetime)

    def test_parse_ts_naive_gets_utc(self):
        from src.api.routes.data_quality import _parse_ts
        result = _parse_ts("2026-01-15T12:00:00")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_parse_ts_none_returns_none(self):
        from src.api.routes.data_quality import _parse_ts
        assert _parse_ts(None) is None

    def test_parse_ts_empty_returns_none(self):
        from src.api.routes.data_quality import _parse_ts
        assert _parse_ts("") is None

    def test_parse_ts_invalid_returns_none(self):
        from src.api.routes.data_quality import _parse_ts
        assert _parse_ts("not-a-date") is None

    def test_cache_age_seconds_recent(self):
        from src.api.routes.data_quality import _cache_age_seconds
        now = datetime.now(timezone.utc)
        payload = {"generated_at": now.isoformat(), "ok": True}
        age = _cache_age_seconds(payload)
        assert age is not None
        assert age >= 0
        assert age < 5  # freshly generated

    def test_cache_age_seconds_old(self):
        from src.api.routes.data_quality import _cache_age_seconds
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        payload = {"generated_at": old.isoformat(), "ok": True}
        age = _cache_age_seconds(payload)
        assert age is not None
        assert age >= 3590

    def test_cache_age_seconds_missing_key_returns_none(self):
        from src.api.routes.data_quality import _cache_age_seconds
        assert _cache_age_seconds({}) is None
