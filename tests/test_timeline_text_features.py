import asyncio
import json
from datetime import datetime, timezone

import src.pipeline.timeline_text_features as features
from src.pipeline.text_normalizer import source_fingerprint


def _event(**overrides):
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "entity_id": "00000000-0000-0000-0000-000000000002",
        "occurred_at": datetime(2026, 8, 8, tzinfo=timezone.utc),
        "source": "telegram",
        "event_type": "MESSAGE_SENT",
        "source_record_id": "chat:1",
        "title": "hello @friend",
        "detail": None,
        "metadata": {"target_preview": "see #topic https://example.org"},
        "existing_source_fingerprint": None,
    }
    row.update(overrides)
    return row


def _candidate(event=None, **overrides):
    event = event or _event()
    row = {
        "event_id": event["id"],
        "occurred_at": event["occurred_at"],
        "existing_source_fingerprint": event.get("existing_source_fingerprint"),
        "already_featured": event.get("existing_source_fingerprint") is not None,
    }
    row.update(overrides)
    return row


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _Conn:
    def __init__(self, fetches):
        self.fetches = list(fetches)
        self.fetch_calls = []
        self.executemany_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self.fetches.pop(0)

    async def executemany(self, sql, rows):
        self.executemany_calls.append((sql, list(rows)))


def test_build_feature_record_keeps_collector_provenance_and_counts():
    record = features.build_feature_record(_event(), max_chars=8000)

    assert record is not None
    assert record["event_id"] == "00000000-0000-0000-0000-000000000001"
    assert record["source"] == "telegram"
    assert record["event_type"] == "MESSAGE_SENT"
    assert record["source_record_id"] == "chat:1"
    assert "hello @friend" in record["canonical_text"]
    assert record["mention_count"] == 1
    assert record["hashtag_count"] == 1
    assert record["url_count"] == 1
    assert len(record["source_fingerprint"]) == 64


def test_build_feature_record_skips_rows_without_selected_text():
    record = features.build_feature_record(_event(
        title=None,
        detail=None,
        metadata={"unselected_blob": "internal collector noise"},
    ))

    assert record is None


def test_build_timeline_text_features_writes_missing_rows(monkeypatch):
    first_event = _event()
    second_event = _event(
        id="00000000-0000-0000-0000-000000000003",
        source="instagram",
        event_type="CONTENT_PUBLISHED",
        source_record_id="post-1",
        title="caption text",
        metadata={"caption": "caption #tag"},
    )
    conn = _Conn([
        [_candidate(first_event), _candidate(second_event)],
        [first_event, second_event],
        [],
    ])
    monkeypatch.setattr(features, "get_analyzer_pool", lambda: _Pool(conn))

    stats = asyncio.run(features.build_timeline_text_features(batch_size=10, max_events=10))

    assert stats["processed"] == 2
    assert stats["inserted"] == 2
    assert stats["by_source"]["telegram"]["processed"] == 1
    assert stats["by_source"]["instagram"]["inserted"] == 1
    candidate_sql, candidate_args = conn.fetch_calls[0]
    assert "FROM timeline_embeddings emb" in candidate_sql
    assert "LEFT JOIN timeline_text_features" in candidate_sql
    assert candidate_args == (10, False, 0)
    detail_sql, detail_args = conn.fetch_calls[1]
    assert "JOIN timeline_events te" in detail_sql
    assert "te.occurred_at = candidates.occurred_at" in detail_sql
    assert "te.occurred_at >= $5" in detail_sql
    assert detail_args[0] == [first_event["id"], second_event["id"]]
    assert stats["candidate_source"] == "timeline_embeddings"
    insert_sql, rows = conn.executemany_calls[0]
    assert "INSERT INTO timeline_text_features" in insert_sql
    assert len(rows) == 2
    selected_metadata = json.loads(rows[0][9])
    assert selected_metadata["target_preview"].startswith("see #topic")


def test_build_timeline_text_features_skips_empty_text_rows(monkeypatch):
    empty_event = _event(
        title=None,
        detail=None,
        metadata={"unselected_blob": "internal collector noise"},
    )
    conn = _Conn([[_candidate(empty_event)], [empty_event], []])
    monkeypatch.setattr(features, "get_analyzer_pool", lambda: _Pool(conn))

    stats = asyncio.run(features.build_timeline_text_features(batch_size=10, max_events=10))

    assert stats["processed"] == 1
    assert stats["inserted"] == 0
    assert stats["skipped_empty_text"] == 1
    assert stats["skipped_count"] == 1
    assert conn.executemany_calls == []


def test_build_timeline_text_features_skips_unchanged_rows(monkeypatch):
    row = _event()
    row["existing_source_fingerprint"] = source_fingerprint(row)
    conn = _Conn([[_candidate(row)], [row], []])
    monkeypatch.setattr(features, "get_analyzer_pool", lambda: _Pool(conn))

    stats = asyncio.run(features.build_timeline_text_features(batch_size=10, max_events=10))

    assert stats["processed"] == 1
    assert stats["inserted"] == 0
    assert stats["skipped_unchanged"] == 1
    candidate_sql, _candidate_args = conn.fetch_calls[0]
    detail_sql, _detail_args = conn.fetch_calls[1]
    assert "FROM timeline_embeddings emb" in candidate_sql
    assert "JOIN timeline_events te" in detail_sql
    assert conn.executemany_calls == []
