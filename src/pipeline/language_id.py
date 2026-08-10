from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

LANGUAGE_ID_VERSION = "language-id-v1"
_WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)
_LATIN_RE = re.compile(r"[A-Za-z]")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")
_TAMIL_RE = re.compile(r"[\u0b80-\u0bff]")
_ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
_URL_ONLY_RE = re.compile(r"^(?:https?://\S+|\W|\d|\s)+$", re.IGNORECASE)
_MALAY_HINTS = {
    "aku", "awak", "saya", "mereka", "kita", "dengan", "untuk", "tidak",
    "bukan", "yang", "dan", "ke", "di", "ini", "itu",
}
_INDONESIAN_HINTS = {
    "gue", "kamu", "mereka", "kita", "dengan", "untuk", "tidak", "bukan",
    "yang", "dan", "ke", "di", "ini", "itu", "banget",
}


@dataclass(frozen=True)
class LanguageProfile:
    primary_language: str
    primary_confidence: float
    candidates: list[dict[str, Any]]
    code_mixed: bool
    flags: dict[str, Any]
    detector: str
    detector_version: str


_fasttext_model = None
_fasttext_load_attempted = False


def _token_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def _load_fasttext_model():
    global _fasttext_model, _fasttext_load_attempted
    if _fasttext_load_attempted:
        return _fasttext_model
    _fasttext_load_attempted = True
    model_path = os.getenv("FASTTEXT_LID_MODEL_PATH")
    if not model_path:
        return None
    try:
        import fasttext  # type: ignore

        _fasttext_model = fasttext.load_model(model_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fastText language model unavailable, using fallback detector: %s", exc)
        _fasttext_model = None
    return _fasttext_model


def _detect_fasttext(text: str) -> LanguageProfile | None:
    model = _load_fasttext_model()
    if model is None:
        return None
    labels, scores = model.predict(text.replace("\n", " "), k=3)
    candidates = []
    for label, score in zip(labels, scores):
        lang = str(label).replace("__label__", "")
        candidates.append({"language": lang, "confidence": round(float(score), 4)})
    if not candidates:
        return None
    top = candidates[0]
    code_mixed = len(candidates) > 1 and float(candidates[1]["confidence"]) >= 0.25
    flags = {"low_confidence": float(top["confidence"]) < 0.65}
    return LanguageProfile(
        primary_language=str(top["language"]),
        primary_confidence=float(top["confidence"]),
        candidates=candidates,
        code_mixed=code_mixed,
        flags={k: v for k, v in flags.items() if v},
        detector="fasttext",
        detector_version=LANGUAGE_ID_VERSION,
    )


def _fallback_detect(text: str) -> LanguageProfile:
    clean = (text or "").strip()
    tokens = [token.lower() for token in _WORD_RE.findall(clean)]
    flags: dict[str, Any] = {}
    if not clean:
        return LanguageProfile("und", 0.0, [], False, {"empty_text": True}, "fallback", LANGUAGE_ID_VERSION)
    if _URL_ONLY_RE.match(clean):
        return LanguageProfile("und", 0.1, [{"language": "und", "confidence": 0.1}], False, {"non_linguistic": True}, "fallback", LANGUAGE_ID_VERSION)
    if len(tokens) < 4:
        flags["too_short"] = True

    counts = {
        "zh": len(_CJK_RE.findall(clean)),
        "hi": len(_DEVANAGARI_RE.findall(clean)),
        "ta": len(_TAMIL_RE.findall(clean)),
        "ar": len(_ARABIC_RE.findall(clean)),
        "latin": len(_LATIN_RE.findall(clean)),
    }
    script_total = counts["zh"] + counts["hi"] + counts["ta"] + counts["ar"] + counts["latin"]
    candidates: list[dict[str, Any]] = []
    for lang in ("zh", "hi", "ta", "ar"):
        if counts[lang]:
            candidates.append({"language": lang, "confidence": round(counts[lang] / max(script_total, 1), 4)})

    latin_lang = "en"
    if tokens:
        ms_score = sum(1 for token in tokens if token in _MALAY_HINTS)
        id_score = sum(1 for token in tokens if token in _INDONESIAN_HINTS)
        if max(ms_score, id_score) >= 2:
            latin_lang = "ms" if ms_score >= id_score else "id"
    if counts["latin"]:
        candidates.append({"language": latin_lang, "confidence": round(counts["latin"] / max(script_total, 1), 4)})

    if not candidates:
        return LanguageProfile("und", 0.2, [{"language": "und", "confidence": 0.2}], False, {"non_linguistic": True}, "fallback", LANGUAGE_ID_VERSION)

    candidates.sort(key=lambda item: float(item["confidence"]), reverse=True)
    top = candidates[0]
    code_mixed = len(candidates) > 1 and float(candidates[1]["confidence"]) >= 0.2
    confidence = min(0.95, max(float(top["confidence"]), 0.55 if not flags.get("too_short") else 0.35))
    flags["low_confidence"] = confidence < 0.6
    return LanguageProfile(
        primary_language=str(top["language"]),
        primary_confidence=round(confidence, 4),
        candidates=candidates,
        code_mixed=code_mixed,
        flags={k: v for k, v in flags.items() if v},
        detector="fallback",
        detector_version=LANGUAGE_ID_VERSION,
    )


def detect_language_profile(text: str) -> LanguageProfile:
    if _token_count(text) >= 4:
        profile = _detect_fasttext(text)
        if profile is not None:
            return profile
    return _fallback_detect(text)


async def backfill_language_profiles(
    *,
    batch_size: int = 500,
    max_events: int | None = None,
    source: str | None = None,
    language: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    max_events = max_events if max_events is not None else batch_size
    pool = get_analyzer_pool()
    args: list[Any] = [min(batch_size, max_events), LANGUAGE_ID_VERSION]
    filters = [
        "ttf.canonical_text IS NOT NULL",
        "(lp.event_id IS NULL OR lp.detector_version <> $2)",
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
            SELECT ttf.event_id::text, ttf.source, ttf.canonical_text
            FROM timeline_text_features ttf
            LEFT JOIN timeline_language_profiles lp ON lp.event_id = ttf.event_id
            WHERE {' AND '.join(filters)}
            ORDER BY ttf.occurred_at DESC
            LIMIT $1
            """,
            *args,
        )

    writes = []
    by_language: dict[str, int] = {}
    skipped = 0
    for row in rows:
        profile = detect_language_profile(row["canonical_text"])
        if profile.primary_language == "und" and profile.flags.get("non_linguistic"):
            skipped += 1
        by_language[profile.primary_language] = by_language.get(profile.primary_language, 0) + 1
        writes.append((
            row["event_id"],
            profile.primary_language,
            profile.primary_confidence,
            json.dumps(profile.candidates),
            profile.code_mixed,
            json.dumps(profile.flags),
            profile.detector,
            profile.detector_version,
        ))

    if writes and not dry_run:
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO timeline_language_profiles (
                    event_id, primary_language, primary_confidence,
                    language_candidates_json, code_mixed, flags,
                    detector, detector_version, processed_at
                )
                VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6::jsonb, $7, $8, NOW())
                ON CONFLICT (event_id) DO UPDATE SET
                    primary_language = EXCLUDED.primary_language,
                    primary_confidence = EXCLUDED.primary_confidence,
                    language_candidates_json = EXCLUDED.language_candidates_json,
                    code_mixed = EXCLUDED.code_mixed,
                    flags = EXCLUDED.flags,
                    detector = EXCLUDED.detector,
                    detector_version = EXCLUDED.detector_version,
                    processed_at = NOW()
                """,
                writes,
            )

    return {
        "processed": len(rows),
        "written": 0 if dry_run else len(writes),
        "dry_run": dry_run,
        "skipped_non_linguistic": skipped,
        "by_language": by_language,
        "detector_version": LANGUAGE_ID_VERSION,
    }
