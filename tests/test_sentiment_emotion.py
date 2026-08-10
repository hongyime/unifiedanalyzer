import asyncio

import src.pipeline.sentiment_emotion as sentiment


def test_sentiment_handles_negation_and_caps():
    result = sentiment.analyze_text_sentiment("I do NOT love this terrible mess!!!", token_count=7)

    assert result.language_code == "en"
    assert result.sentiment_label == "negative"
    assert result.vader_compound is not None
    assert result.flags["all_caps_emphasis"] is True


def test_sentiment_scores_afinn_and_nrc_emotions():
    result = sentiment.analyze_text_sentiment("happy love safe win", token_count=4)

    assert result.afinn_score > 0
    assert result.nrc_emotions["joy"] > 0
    assert result.nrc_emotions["trust"] > 0
    assert result.sentiment_confidence > 0


def test_sentiment_flags_unsupported_language():
    result = sentiment.analyze_text_sentiment("非常生气非常难过", token_count=1)

    assert result.language_code == "unsupported"
    assert result.flags["unsupported_language"] is True
    assert result.sentiment_confidence <= 0.35


def test_enrich_timeline_sentiment_writes_updates(monkeypatch):
    class Conn:
        def __init__(self):
            self.updates = []

        async def fetch(self, sql, *args):
            assert "FROM timeline_text_features" in sql
            return [{
                "event_id": "00000000-0000-0000-0000-000000000001",
                "source": "telegram",
                "canonical_text": "I love this great update",
                "token_count": 5,
                "method_versions": {},
                "profile_language": "en",
                "translated_text": None,
                "translator_version": None,
            }]

        async def executemany(self, sql, rows):
            assert "UPDATE timeline_text_features" in sql
            self.updates.extend(rows)

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
    monkeypatch.setattr(sentiment, "get_analyzer_pool", lambda: pool)

    stats = asyncio.run(sentiment.enrich_timeline_sentiment(batch_size=10, max_events=10))

    assert stats["processed"] == 1
    assert stats["updated"] == 1
    assert pool.conn.updates[0][9] == "positive"


def test_sentiment_scores_machine_translation_as_context():
    result = sentiment.analyze_text_sentiment(
        "I love this great update",
        token_count=5,
        source_language="zh",
        machine_translated=True,
    )

    assert result.language_code == "zh"
    assert result.sentiment_label == "positive"
    assert result.flags["machine_translated"] is True
