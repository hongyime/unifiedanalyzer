from pathlib import Path

import pytest

from src.pipeline import media_analysis


def test_looks_like_pdf_rejects_html_error_page(tmp_path: Path):
    path = tmp_path / "not-a-real.pdf"
    path.write_bytes(b"<!DOCTYPE html><title>rate limited</title>")

    assert media_analysis._looks_like_pdf(path) is False


def test_looks_like_pdf_accepts_pdf_header_with_leading_whitespace(tmp_path: Path):
    path = tmp_path / "real.pdf"
    path.write_bytes(b"\n  %PDF-1.7\n")

    assert media_analysis._looks_like_pdf(path) is True


def test_extract_pdf_text_skips_non_pdf_without_invoking_parser(monkeypatch, tmp_path: Path):
    path = tmp_path / "not-a-real.pdf"
    path.write_bytes(b"<!DOCTYPE html><title>rate limited</title>")

    def fail_if_called(_path):
        raise AssertionError("PdfReader should not be called for a non-PDF header")

    monkeypatch.setattr(media_analysis.pypdf, "PdfReader", fail_if_called)

    assert media_analysis._extract_pdf_text(path) == ""


@pytest.mark.asyncio
async def test_entity_text_corpus_reloads_all_saved_text_rows(monkeypatch):
    class _Connection:
        async def fetch(self, query, analysis_type):
            assert "FROM media_analysis" in query
            assert "extracted_text IS NOT NULL" in query
            assert analysis_type == "pdf_text"
            return [
                {"media_item_id": "older", "source": "telegram", "extracted_text": "older@example.com"},
                {"media_item_id": "latest", "source": "telegram", "extracted_text": "latest@example.com"},
            ]

    class _Acquire:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_args):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    conn = _Connection()

    async def fetch_item_entities(ids):
        assert ids == ["older", "latest"]
        return {
            "older": ("telegram", "old-platform-id"),
            "latest": ("telegram", "new-platform-id"),
        }

    monkeypatch.setattr(media_analysis, "get_analyzer_pool", lambda: _Pool())
    monkeypatch.setattr(media_analysis, "fetch_media_item_entities", fetch_item_entities)

    result = await media_analysis._build_entity_text_corpus(
        "pdf_text",
        {
            ("telegram", "old-platform-id"): "entity-old",
            ("telegram", "new-platform-id"): "entity-new",
        },
    )

    assert result == {
        "entity-old": [("telegram", "older@example.com")],
        "entity-new": [("telegram", "latest@example.com")],
    }


@pytest.mark.parametrize(
    ("analyze", "analysis_type"),
    [
        (media_analysis.analyze_media_pdf_text, "pdf_text"),
        (media_analysis.analyze_media_office_text, "office_text"),
    ],
)
@pytest.mark.asyncio
async def test_media_text_signals_use_the_full_saved_corpus(monkeypatch, analyze, analysis_type):
    stored_corpus = {
        "entity-old": [("telegram", "older@example.com")],
        "entity-new": [("telegram", "latest@example.com")],
    }
    emitted = []
    rebuilt = []
    current_item = {
        "id": "latest",
        "source": "telegram",
        "entity_id": "new-platform-id",
        "content_type": "pdf" if analysis_type == "pdf_text" else "docx",
        "file_path": "fixture",
    }

    async def fetch_unprocessed(_pairs, got_type, *, limit):
        assert got_type == analysis_type
        assert limit == 1
        return [current_item]

    async def upsert(rows):
        assert rows[0]["analysis_type"] == analysis_type
        return len(rows)

    async def build_lookup():
        return {("telegram", "new-platform-id"): "entity-new"}

    async def build_corpus(got_type, entity_lookup):
        rebuilt.append((got_type, entity_lookup))
        return stored_corpus

    async def emit(entity_texts, _lookups, source_column):
        emitted.append((entity_texts, source_column))
        return {"emitted": 1}

    async def no_args():
        return object()

    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(media_analysis, "_drive_available", lambda: True)
    monkeypatch.setattr(media_analysis.asyncio, "to_thread", run_inline)
    monkeypatch.setattr(media_analysis, "fetch_unprocessed_media", fetch_unprocessed)
    monkeypatch.setattr(media_analysis, "resolve_media_path", lambda _path: Path("fixture"))
    monkeypatch.setattr(media_analysis, "upsert_media_analysis", upsert)
    monkeypatch.setattr(media_analysis, "build_entity_lookup", build_lookup)
    monkeypatch.setattr(media_analysis, "_build_entity_text_corpus", build_corpus)
    monkeypatch.setattr(media_analysis, "build_contact_lookups", no_args)
    monkeypatch.setattr(media_analysis, "emit_media_contact_signals", emit)
    if analysis_type == "pdf_text":
        monkeypatch.setattr(media_analysis, "_looks_like_pdf", lambda _path: True)
        monkeypatch.setattr(media_analysis, "_extract_pdf_text", lambda _path: "latest@example.com")
    else:
        monkeypatch.setattr(media_analysis, "office_text_available", lambda: True)
        monkeypatch.setattr(media_analysis, "_extract_office_text", lambda _path, _kind: "latest@example.com")

    stats = await analyze(limit=1)

    assert stats["processed"] == 1
    assert rebuilt == [(analysis_type, {("telegram", "new-platform-id"): "entity-new"})]
    assert emitted == [(stored_corpus, analysis_type)]
