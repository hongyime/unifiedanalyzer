import asyncio

import src.pipeline.translation_worker as translation_worker


class FakeTranslator:
    name = "fake"
    version = "fake-v1"

    def translate(self, text, source_language, target_language):
        if "fail" in text:
            raise RuntimeError("boom")
        return "I love this update"


def test_translation_decision_skips_english_and_short_text():
    assert translation_worker.translation_decision(source_language="en", token_count=10).should_translate is False
    assert translation_worker.translation_decision(source_language="zh", token_count=2).reason == "too_short"
    assert translation_worker.translation_decision(source_language="zh", token_count=2, watched=True).should_translate is True


def test_translation_provider_model_helpers(monkeypatch):
    monkeypatch.delenv("TRANSLATION_OPUS_MODEL_ZH_EN", raising=False)

    assert translation_worker.normalize_translation_language("zh-Hans") == "zh"
    assert translation_worker.opus_model_name("zh-Hans", "en") == "Helsinki-NLP/opus-mt-zh-en"
    assert translation_worker.nllb_language_code("ms") == "zsm_Latn"

    monkeypatch.setenv("TRANSLATION_OPUS_MODEL_ZH_EN", "local/zh-en")
    assert translation_worker.opus_model_name("zh", "en") == "local/zh-en"


def test_translation_provider_falls_back_to_noop(monkeypatch):
    monkeypatch.setenv("TRANSLATION_PROVIDER", "opus-mt")
    monkeypatch.setattr(translation_worker, "OpusMtTranslator", lambda: (_ for _ in ()).throw(RuntimeError("missing")))

    translator = translation_worker.get_translator()

    assert translator.name == "noop"


def test_translation_max_per_run_env(monkeypatch):
    monkeypatch.setenv("TRANSLATION_MAX_PER_RUN", "12")
    assert translation_worker.translation_max_per_run() == 12

    monkeypatch.setenv("TRANSLATION_MAX_PER_RUN", "invalid")
    assert translation_worker.translation_max_per_run() == 500


def test_translation_backfill_is_idempotent_shape_and_stores_failures(monkeypatch):
    class Conn:
        def __init__(self):
            self.writes = []

        async def fetch(self, sql, *args):
            assert "timeline_translations" in sql
            return [
                {
                    "event_id": "00000000-0000-0000-0000-000000000001",
                    "source": "telegram",
                    "text_sha1": "a" * 40,
                    "canonical_text": "我很喜欢这个更新",
                    "token_count": 5,
                    "source_language": "zh",
                    "watched": False,
                },
                {
                    "event_id": "00000000-0000-0000-0000-000000000002",
                    "source": "telegram",
                    "text_sha1": "b" * 40,
                    "canonical_text": "fail now please",
                    "token_count": 5,
                    "source_language": "zh",
                    "watched": False,
                },
            ]

        async def executemany(self, sql, rows):
            assert "INSERT INTO timeline_translations" in sql
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
    monkeypatch.setattr(translation_worker, "get_analyzer_pool", lambda: pool)

    stats = asyncio.run(translation_worker.run_translation_backfill(
        batch_size=10,
        max_events=10,
        translator=FakeTranslator(),
    ))

    assert stats["processed"] == 2
    assert stats["translated"] == 1
    assert stats["failed"] == 1
    assert pool.conn.writes[0][7] == "translated"
    assert pool.conn.writes[1][7] == "failed"
