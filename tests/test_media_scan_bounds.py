from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_face_worker_bounds_collector_candidate_query():
    text = (REPO_ROOT / "src" / "face_worker.py").read_text(encoding="utf-8")

    assert "FACE_COLLECTOR_MEDIA_SCAN_WINDOW" in text
    assert "LIMIT :scan_window" in text
    assert "content_type = ANY(:cts)" in text


def test_async_media_fetch_uses_sql_limit_for_bounded_batches():
    text = (REPO_ROOT / "src" / "pipeline" / "media_common.py").read_text(encoding="utf-8")

    assert "query_limit = max(remaining * 20, remaining + 100)" in text
    assert "LIMIT $3" in text
    assert "LIMIT $2" in text
