import asyncio

from src.api.routes.multilingual import multilingual_status


def test_multilingual_status_reports_counts(monkeypatch):
    class Conn:
        async def fetchrow(self, sql, *args):
            return {
                "text_rows": 100,
                "profile_rows": 80,
                "code_mixed_rows": 3,
                "unsupported_rows": 4,
                "translation_rows": 20,
                "translated_rows": 15,
                "failed_translation_rows": 2,
                "skipped_translation_rows": 3,
            }

        async def fetch(self, sql, *args):
            if "primary_language" in sql:
                return [{"language": "en", "count": 60}, {"language": "zh", "count": 20}]
            return [{"reason": "missing model", "count": 2}]

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *_exc):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    monkeypatch.setattr("src.api.routes.multilingual.get_analyzer_pool", lambda: Pool())

    result = asyncio.run(multilingual_status())

    assert result["profile_coverage_pct"] == 80.0
    assert result["translation_coverage_pct"] == 18.8
    assert result["languages"][1]["language"] == "zh"
    assert result["failures"][0]["reason"] == "missing model"
    assert result["language_detector"]["fallback_detector"] is True
    assert result["translation_worker"]["bounded_worker"] is True
    assert result["translation_worker"]["nllb_default_off"] is True
