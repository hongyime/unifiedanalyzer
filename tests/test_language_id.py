import asyncio
import json

import src.pipeline.language_id as language_id


def test_language_profile_detects_basic_scripts_and_code_mix():
    zh = language_id.detect_language_profile("今晚一起吃饭然后 walk home")

    assert zh.primary_language in {"zh", "en"}
    assert zh.code_mixed is True
    assert any(c["language"] == "zh" for c in zh.candidates)


def test_language_profile_flags_non_linguistic_and_short_text():
    url = language_id.detect_language_profile("https://example.com !!!")
    short = language_id.detect_language_profile("ok")

    assert url.primary_language == "und"
    assert url.flags["non_linguistic"] is True
    assert short.flags["too_short"] is True


def test_language_backfill_writes_profiles(monkeypatch):
    class Conn:
        def __init__(self):
            self.writes = []

        async def fetch(self, sql, *args):
            assert "timeline_language_profiles" in sql
            return [{
                "event_id": "00000000-0000-0000-0000-000000000001",
                "source": "telegram",
                "canonical_text": "hello this is a useful update",
            }]

        async def executemany(self, sql, rows):
            assert "INSERT INTO timeline_language_profiles" in sql
            self.writes.extend(rows)

    class Acquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *_exc):
            return False

    class Pool:
        def __init__(self):
            self.conn = Conn()

        def acquire(self):
            return Acquire(self.conn)

    pool = Pool()
    monkeypatch.setattr(language_id, "get_analyzer_pool", lambda: pool)

    stats = asyncio.run(language_id.backfill_language_profiles(batch_size=10, max_events=10))

    assert stats["processed"] == 1
    assert stats["written"] == 1
    assert stats["by_language"]["en"] == 1
    candidates = json.loads(pool.conn.writes[0][3])
    assert candidates[0]["language"] == "en"
