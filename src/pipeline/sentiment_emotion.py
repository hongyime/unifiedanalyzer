from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

SENTIMENT_VERSION = "sentiment-emotion-v1"
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']+")
_NON_LATIN_RE = re.compile(r"[^\W\d_]", re.UNICODE)

_FALLBACK_AFINN = {
    "bad": -3, "hate": -3, "hated": -3, "awful": -3, "terrible": -3,
    "angry": -3, "sad": -2, "scared": -2, "worse": -3, "worst": -3,
    "good": 3, "great": 3, "love": 3, "loved": 3, "happy": 3,
    "excellent": 4, "win": 3, "winning": 4, "safe": 2,
}
_FALLBACK_NRC = {
    "angry": ("anger",), "rage": ("anger",), "hate": ("anger", "disgust"),
    "fear": ("fear",), "scared": ("fear",), "worried": ("fear",),
    "sad": ("sadness",), "cry": ("sadness",), "grief": ("sadness",),
    "happy": ("joy",), "love": ("joy", "trust"), "great": ("joy",),
    "safe": ("trust",), "win": ("anticipation", "joy"),
}
_EMOTIONS = ("anger", "anticipation", "disgust", "fear", "joy", "sadness", "surprise", "trust")


@dataclass(frozen=True)
class SentimentRecord:
    language_code: str
    language_confidence: float
    vader_compound: float | None
    vader_pos: float | None
    vader_neu: float | None
    vader_neg: float | None
    afinn_score: float
    nrc_emotions: dict[str, float]
    sentiment_label: str
    sentiment_confidence: float
    flags: dict[str, Any]
    method_versions: dict[str, str]


_vader = None
_afinn = None
_nrc_lexicon: dict[str, tuple[str, ...]] | None = None


def _get_vader():
    global _vader
    if _vader is not None:
        return _vader or None
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _vader = SentimentIntensityAnalyzer()
    except Exception as exc:  # noqa: BLE001
        logger.debug("VADER unavailable, using lexical fallback: %s", exc)
        _vader = False
    return _vader or None


def _get_afinn():
    global _afinn
    if _afinn is not None:
        return _afinn or None
    try:
        from afinn import Afinn
        _afinn = Afinn()
    except Exception as exc:  # noqa: BLE001
        logger.debug("AFINN unavailable, using fallback lexicon: %s", exc)
        _afinn = False
    return _afinn or None


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text or "")]


def detect_language(text: str) -> tuple[str, float, dict[str, Any]]:
    if not text.strip():
        return "und", 0.0, {"empty_text": True}
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    letters = len(_NON_LATIN_RE.findall(text))
    if letters and latin_chars / max(letters, 1) < 0.5:
        return "unsupported", 0.55, {"unsupported_language": True}
    return "en", min(0.99, 0.55 + latin_chars / max(len(text), 1)), {}


def _fallback_vader(text: str, words: list[str]) -> dict[str, float]:
    score = sum(_FALLBACK_AFINN.get(w, 0) for w in words)
    for idx, word in enumerate(words[:-1]):
        if word in {"not", "never"}:
            next_score = _FALLBACK_AFINN.get(words[idx + 1], 0)
            if next_score > 0:
                score -= next_score * 1.5
    if "terrible" in words or "awful" in words or "hate" in words:
        score -= 1
    if any(ch in text for ch in ("!", "😡", "😭", "😍", "❤️")):
        score *= 1.15
    compound = math.tanh(score / 5.0)
    return {
        "compound": compound,
        "pos": max(compound, 0.0),
        "neu": max(0.0, 1.0 - abs(compound)),
        "neg": max(-compound, 0.0),
    }


def _score_afinn(text: str, words: list[str]) -> float:
    afinn = _get_afinn()
    if afinn is not None:
        try:
            return float(afinn.score(text))
        except Exception:  # noqa: BLE001
            pass
    return float(sum(_FALLBACK_AFINN.get(w, 0) for w in words))


def _score_nrc(words: list[str]) -> dict[str, float]:
    counts = {emotion: 0.0 for emotion in _EMOTIONS}
    total = 0
    for word in words:
        emotions = _FALLBACK_NRC.get(word, ())
        for emotion in emotions:
            counts[emotion] += 1.0
            total += 1
    if total:
        return {key: round(value / total, 4) for key, value in counts.items() if value > 0}
    return {}


def analyze_text_sentiment(
    text: str,
    *,
    token_count: int | None = None,
    source_language: str | None = None,
    machine_translated: bool = False,
) -> SentimentRecord:
    text = text or ""
    words = _tokens(text)
    if machine_translated:
        language_code = source_language or "und"
        language_confidence = 0.7
        flags = {"machine_translated": True, "scored_language": "en"}
        scoring_language = "en"
    else:
        language_code, language_confidence, flags = detect_language(text)
        scoring_language = language_code
    flags = dict(flags)
    if token_count is None:
        token_count = len(words)
    if token_count < 3:
        flags["too_short"] = True
    if any(word.isupper() and len(word) > 2 for word in re.findall(r"\b\w+\b", text)):
        flags["all_caps_emphasis"] = True

    vader_scores: dict[str, float] | None = None
    if scoring_language == "en":
        vader = _get_vader()
        if vader is not None:
            vader_scores = {k: float(v) for k, v in vader.polarity_scores(text).items()}
        else:
            vader_scores = _fallback_vader(text, words)
    afinn_score = _score_afinn(text, words) if scoring_language == "en" else 0.0
    nrc = _score_nrc(words) if scoring_language == "en" else {}

    compound = float(vader_scores["compound"]) if vader_scores else 0.0
    if compound >= 0.05:
        label = "positive"
    elif compound <= -0.05:
        label = "negative"
    else:
        label = "neutral"
    confidence = min(1.0, max(abs(compound), min(abs(afinn_score) / 8.0, 1.0)))
    if flags.get("too_short") or flags.get("unsupported_language"):
        confidence = min(confidence, 0.35)

    return SentimentRecord(
        language_code=language_code,
        language_confidence=round(float(language_confidence), 4),
        vader_compound=round(compound, 4) if vader_scores else None,
        vader_pos=round(float(vader_scores["pos"]), 4) if vader_scores else None,
        vader_neu=round(float(vader_scores["neu"]), 4) if vader_scores else None,
        vader_neg=round(float(vader_scores["neg"]), 4) if vader_scores else None,
        afinn_score=round(afinn_score, 4),
        nrc_emotions=nrc,
        sentiment_label=label,
        sentiment_confidence=round(float(confidence), 4),
        flags=flags,
        method_versions={"sentiment_emotion": SENTIMENT_VERSION},
    )


async def enrich_timeline_sentiment(batch_size: int = 1000, max_events: int | None = None) -> dict:
    max_events = max_events if max_events is not None else int(os.getenv("SENTIMENT_BATCH_PER_RUN", "5000"))
    pool = get_analyzer_pool()
    processed = 0
    updated = 0
    skipped_unchanged = 0
    by_source: dict[str, dict[str, int]] = {}

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ttf.event_id::text,
                   ttf.source,
                   ttf.canonical_text,
                   ttf.token_count,
                   ttf.method_versions,
                   COALESCE(lp.primary_language, ttf.language_code) AS profile_language,
                   tr.translated_text,
                   tr.translator_version
            FROM timeline_text_features ttf
            LEFT JOIN timeline_language_profiles lp ON lp.event_id = ttf.event_id
            LEFT JOIN LATERAL (
                SELECT translated_text, translator_version
                FROM timeline_translations tr
                WHERE tr.event_id = ttf.event_id
                  AND tr.target_language = 'en'
                  AND tr.status = 'translated'
                  AND NULLIF(BTRIM(tr.translated_text), '') IS NOT NULL
                ORDER BY tr.updated_at DESC
                LIMIT 1
            ) tr ON TRUE
            WHERE ttf.canonical_text IS NOT NULL
              AND (
                ttf.processed_at IS NULL
                OR ttf.sentiment_label IS NULL
                OR COALESCE(ttf.method_versions->>'sentiment_emotion', '') <> $2
              )
            ORDER BY ttf.occurred_at DESC
            LIMIT $1
            """,
            min(batch_size, max_events),
            SENTIMENT_VERSION,
        )

    if not rows:
        return {"processed": 0, "updated": 0, "skipped_unchanged": 0, "by_source": {}}

    updates = []
    for row in rows:
        processed += 1
        source = row["source"] or "unknown"
        by_source.setdefault(source, {"processed": 0, "updated": 0, "skipped_count": 0})
        by_source[source]["processed"] += 1
        profile_language = (row["profile_language"] or "").lower()
        translated_text = row["translated_text"]
        use_translation = bool(translated_text and profile_language and profile_language not in {"en", "und"})
        record = analyze_text_sentiment(
            translated_text if use_translation else row["canonical_text"],
            token_count=row["token_count"],
            source_language=profile_language or None,
            machine_translated=use_translation,
        )
        method_versions = row["method_versions"] if isinstance(row["method_versions"], dict) else {}
        method_versions = {**method_versions, **record.method_versions}
        if use_translation:
            method_versions["translation"] = row["translator_version"] or "unknown"
        updates.append((
            row["event_id"],
            record.language_code,
            record.language_confidence,
            record.vader_compound,
            record.vader_pos,
            record.vader_neu,
            record.vader_neg,
            record.afinn_score,
            json.dumps(record.nrc_emotions),
            record.sentiment_label,
            record.sentiment_confidence,
            json.dumps(record.flags),
            json.dumps(method_versions),
        ))
        updated += 1
        by_source[source]["updated"] += 1

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            UPDATE timeline_text_features
            SET language_code = $2,
                language_confidence = $3,
                vader_compound = $4,
                vader_pos = $5,
                vader_neu = $6,
                vader_neg = $7,
                afinn_score = $8,
                nrc_emotions = $9::jsonb,
                sentiment_label = $10,
                sentiment_confidence = $11,
                sentiment_flags = $12::jsonb,
                method_versions = $13::jsonb,
                processed_at = NOW(),
                updated_at = NOW()
            WHERE event_id = $1::uuid
            """,
            updates,
        )

    logger.info("sentiment_emotion: processed=%d updated=%d", processed, updated)
    return {
        "processed": processed,
        "updated": updated,
        "skipped_unchanged": skipped_unchanged,
        "by_source": by_source,
    }
