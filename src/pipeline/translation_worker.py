from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

TRANSLATOR_VERSION = "translation-worker-v1"
DEFAULT_TARGET_LANGUAGE = "en"


class Translator(Protocol):
    name: str
    version: str

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        ...


@dataclass(frozen=True)
class TranslationDecision:
    should_translate: bool
    reason: str | None = None


class NoopTranslator:
    name = "noop"
    version = TRANSLATOR_VERSION

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        raise RuntimeError("No local translator configured")


class TransformersTranslator:
    name = "transformers-opus-mt"
    version = TRANSLATOR_VERSION

    def __init__(self) -> None:
        from transformers import pipeline  # type: ignore

        self._pipeline = pipeline("translation")

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        result = self._pipeline(text, max_length=512)
        if isinstance(result, list) and result:
            translated = result[0].get("translation_text")
            if translated:
                return str(translated)
        raise RuntimeError("translator returned no text")


def get_translator() -> Translator:
    provider = os.getenv("TRANSLATION_PROVIDER", "noop").strip().lower()
    if provider in {"transformers", "opus", "opus-mt"}:
        try:
            return TransformersTranslator()
        except Exception as exc:  # noqa: BLE001
            logger.warning("local translation provider unavailable, using noop: %s", exc)
    return NoopTranslator()


def text_version_hash(text: str, translator_version: str = TRANSLATOR_VERSION) -> str:
    return hashlib.sha1(f"{translator_version}\0{text}".encode("utf-8")).hexdigest()


def translation_decision(
    *,
    source_language: str | None,
    token_count: int,
    watched: bool = False,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> TranslationDecision:
    lang = (source_language or "und").lower()
    if lang in {"", "und", target_language.lower(), "en"}:
        return TranslationDecision(False, "english_or_unknown")
    if token_count < 4 and not watched:
        return TranslationDecision(False, "too_short")
    return TranslationDecision(True)


async def run_translation_backfill(
    *,
    batch_size: int = 100,
    max_events: int | None = None,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
    source: str | None = None,
    language: str | None = None,
    dry_run: bool = False,
    translator: Translator | None = None,
) -> dict[str, Any]:
    max_events = max_events if max_events is not None else min(batch_size, 500)
    translator = translator or get_translator()
    pool = get_analyzer_pool()
    args: list[Any] = [min(batch_size, max_events), target_language, translator.version]
    filters = [
        "ttf.canonical_text IS NOT NULL",
        "(tr.event_id IS NULL OR tr.text_sha1 <> ttf.text_sha1 OR tr.status = 'failed')",
    ]
    if source:
        args.append(source)
        filters.append(f"ttf.source = ${len(args)}")
    if language:
        args.append(language)
        filters.append(f"lp.primary_language = ${len(args)}")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT ttf.event_id::text,
                   ttf.source,
                   ttf.text_sha1,
                   ttf.canonical_text,
                   ttf.token_count,
                   COALESCE(lp.primary_language, ttf.language_code, 'und') AS source_language,
                   COALESCE(e.watch_status IN ('priority', 'watching'), false) AS watched
            FROM timeline_text_features ttf
            LEFT JOIN timeline_language_profiles lp ON lp.event_id = ttf.event_id
            LEFT JOIN entities e ON e.id = ttf.entity_id
            LEFT JOIN timeline_translations tr
              ON tr.event_id = ttf.event_id
             AND tr.target_language = $2
             AND tr.translator_version = $3
            WHERE {' AND '.join(filters)}
            ORDER BY ttf.occurred_at DESC
            LIMIT $1
            """,
            *args,
        )

    writes = []
    processed = 0
    skipped = 0
    failed = 0
    translated = 0
    by_status: dict[str, int] = {}
    for row in rows:
        processed += 1
        decision = translation_decision(
            source_language=row["source_language"],
            token_count=int(row["token_count"] or 0),
            watched=bool(row["watched"]),
            target_language=target_language,
        )
        translated_text = None
        status = "skipped"
        error = decision.reason
        confidence = None
        if decision.should_translate:
            try:
                translated_text = translator.translate(
                    row["canonical_text"],
                    str(row["source_language"]),
                    target_language,
                )
                status = "translated"
                error = None
                confidence = 0.7
                translated += 1
            except Exception as exc:  # noqa: BLE001
                status = "failed"
                error = str(exc)[:500]
                failed += 1
        else:
            skipped += 1
        by_status[status] = by_status.get(status, 0) + 1
        writes.append((
            row["event_id"],
            row["source_language"],
            target_language,
            translated_text,
            translator.name,
            translator.version,
            confidence,
            status,
            error,
            row["text_sha1"],
            json.dumps({"text_version_hash": text_version_hash(row["canonical_text"], translator.version)}),
        ))

    if writes and not dry_run:
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO timeline_translations (
                    event_id, source_language, target_language, translated_text,
                    translator, translator_version, confidence, status, error,
                    text_sha1, metadata, created_at, updated_at
                )
                VALUES (
                    $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11::jsonb, NOW(), NOW()
                )
                ON CONFLICT (event_id, target_language, translator_version) DO UPDATE SET
                    source_language = EXCLUDED.source_language,
                    translated_text = EXCLUDED.translated_text,
                    translator = EXCLUDED.translator,
                    confidence = EXCLUDED.confidence,
                    status = EXCLUDED.status,
                    error = EXCLUDED.error,
                    text_sha1 = EXCLUDED.text_sha1,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                writes,
            )

    return {
        "processed": processed,
        "written": 0 if dry_run else len(writes),
        "translated": translated,
        "skipped": skipped,
        "failed": failed,
        "dry_run": dry_run,
        "target_language": target_language,
        "translator": translator.name,
        "translator_version": translator.version,
        "by_status": by_status,
    }
