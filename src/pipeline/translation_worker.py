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
DEFAULT_NLLB_MODEL = "facebook/nllb-200-distilled-600M"
DEFAULT_OPUS_PREFIX = "Helsinki-NLP/opus-mt"

_OPUS_LANG = {
    "cmn": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh",
    "yue": "zh",
    "id": "id",
    "ind": "id",
    "ms": "ms",
    "msa": "ms",
    "hi": "hi",
    "hin": "hi",
    "ta": "ta",
    "tam": "ta",
}

_NLLB_LANG = {
    "en": "eng_Latn",
    "zh": "zho_Hans",
    "zh-cn": "zho_Hans",
    "zh-hans": "zho_Hans",
    "zh-hant": "zho_Hant",
    "id": "ind_Latn",
    "ms": "zsm_Latn",
    "hi": "hin_Deva",
    "ta": "tam_Taml",
}


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


def normalize_translation_language(language: str | None, *, provider: str = "opus") -> str:
    lang = (language or "und").strip().lower().replace("_", "-")
    if provider == "nllb":
        return _NLLB_LANG.get(lang, lang)
    return _OPUS_LANG.get(lang, lang.split("-")[0])


def opus_model_name(source_language: str, target_language: str = DEFAULT_TARGET_LANGUAGE) -> str:
    source = normalize_translation_language(source_language, provider="opus")
    target = normalize_translation_language(target_language, provider="opus")
    override_key = f"TRANSLATION_OPUS_MODEL_{source.upper()}_{target.upper()}".replace("-", "_")
    override = os.getenv(override_key)
    if override:
        return override
    return f"{os.getenv('TRANSLATION_OPUS_MODEL_PREFIX', DEFAULT_OPUS_PREFIX).rstrip('/')}-{source}-{target}"


def nllb_language_code(language: str) -> str:
    return normalize_translation_language(language, provider="nllb")


class OpusMtTranslator:
    name = "opus-mt"
    version = f"{TRANSLATOR_VERSION}:opus-mt"

    def __init__(self) -> None:
        self._pipelines: dict[tuple[str, str], Any] = {}

    def _pipeline_for(self, source_language: str, target_language: str):
        key = (
            normalize_translation_language(source_language, provider="opus"),
            normalize_translation_language(target_language, provider="opus"),
        )
        if key not in self._pipelines:
            from transformers import pipeline  # type: ignore

            self._pipelines[key] = pipeline(
                "translation",
                model=opus_model_name(key[0], key[1]),
            )
        return self._pipelines[key]

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        result = self._pipeline_for(source_language, target_language)(
            text,
            max_length=int(os.getenv("TRANSLATION_MAX_LENGTH", "512")),
        )
        if isinstance(result, list) and result:
            translated = result[0].get("translation_text")
            if translated:
                return str(translated)
        raise RuntimeError("translator returned no text")


class NllbTranslator:
    name = "nllb-200"
    version = f"{TRANSLATOR_VERSION}:nllb-200-distilled-600m"

    def __init__(self) -> None:
        from transformers import pipeline  # type: ignore

        self._pipeline = pipeline(
            "translation",
            model=os.getenv("TRANSLATION_NLLB_MODEL", DEFAULT_NLLB_MODEL),
        )

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        result = self._pipeline(
            text,
            src_lang=nllb_language_code(source_language),
            tgt_lang=nllb_language_code(target_language),
            max_length=int(os.getenv("TRANSLATION_MAX_LENGTH", "512")),
        )
        if isinstance(result, list) and result:
            translated = result[0].get("translation_text")
            if translated:
                return str(translated)
        raise RuntimeError("translator returned no text")


def get_translator() -> Translator:
    provider = os.getenv("TRANSLATION_PROVIDER", "noop").strip().lower()
    if provider in {"transformers", "opus", "opus-mt"}:
        try:
            return OpusMtTranslator()
        except Exception as exc:  # noqa: BLE001
            logger.warning("local translation provider unavailable, using noop: %s", exc)
    if provider in {"nllb", "nllb-200"}:
        try:
            return NllbTranslator()
        except Exception as exc:  # noqa: BLE001
            logger.warning("local NLLB translation provider unavailable, using noop: %s", exc)
    return NoopTranslator()


def translation_max_per_run() -> int:
    try:
        return max(1, int(os.getenv("TRANSLATION_MAX_PER_RUN", "500")))
    except (TypeError, ValueError):
        return 500


def translation_runtime_status() -> dict[str, Any]:
    provider = os.getenv("TRANSLATION_PROVIDER", "noop").strip().lower() or "noop"
    target_language = os.getenv("TRANSLATION_TARGET_LANGUAGE", DEFAULT_TARGET_LANGUAGE).strip() or DEFAULT_TARGET_LANGUAGE
    try:
        max_length = max(1, int(os.getenv("TRANSLATION_MAX_LENGTH", "512")))
    except (TypeError, ValueError):
        max_length = 512
    return {
        "provider": provider,
        "target_language": target_language,
        "translator_version": TRANSLATOR_VERSION,
        "max_per_run": translation_max_per_run(),
        "max_length": max_length,
        "bounded_worker": True,
        "opus_mt_enabled": provider in {"transformers", "opus", "opus-mt"},
        "opus_model_prefix": os.getenv("TRANSLATION_OPUS_MODEL_PREFIX", DEFAULT_OPUS_PREFIX),
        "nllb_enabled": provider in {"nllb", "nllb-200"},
        "nllb_model": os.getenv("TRANSLATION_NLLB_MODEL", DEFAULT_NLLB_MODEL),
        "nllb_default_off": provider not in {"nllb", "nllb-200"},
    }


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
    max_events = max_events if max_events is not None else min(batch_size, translation_max_per_run())
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
