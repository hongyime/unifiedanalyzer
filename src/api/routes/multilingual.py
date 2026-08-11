from __future__ import annotations

from fastapi import APIRouter

from src.db.connection import get_analyzer_pool

router = APIRouter(tags=["multilingual"])


@router.get("/multilingual/status")
async def multilingual_status():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM timeline_text_features WHERE canonical_text IS NOT NULL)::int AS text_rows,
                (SELECT count(*) FROM timeline_language_profiles)::int AS profile_rows,
                (SELECT count(*) FROM timeline_language_profiles WHERE code_mixed)::int AS code_mixed_rows,
                (SELECT count(*) FROM timeline_language_profiles WHERE primary_language = 'und')::int AS unsupported_rows,
                (SELECT count(*) FROM timeline_translations)::int AS translation_rows,
                (SELECT count(*) FROM timeline_translations WHERE status = 'translated')::int AS translated_rows,
                (SELECT count(*) FROM timeline_translations WHERE status = 'failed')::int AS failed_translation_rows,
                (SELECT count(*) FROM timeline_translations WHERE status = 'skipped')::int AS skipped_translation_rows
            """
        )
        languages = await conn.fetch(
            """
            SELECT primary_language AS language, count(*)::int AS count
            FROM timeline_language_profiles
            GROUP BY primary_language
            ORDER BY count(*) DESC, primary_language
            LIMIT 20
            """
        )
        failures = await conn.fetch(
            """
            SELECT COALESCE(NULLIF(error, ''), status) AS reason, count(*)::int AS count
            FROM timeline_translations
            WHERE status = 'failed'
            GROUP BY COALESCE(NULLIF(error, ''), status)
            ORDER BY count(*) DESC
            LIMIT 10
            """
        )
    text_rows = int(totals["text_rows"] or 0)
    profile_rows = int(totals["profile_rows"] or 0)
    translated_rows = int(totals["translated_rows"] or 0)
    return {
        "text_rows": text_rows,
        "profile_rows": profile_rows,
        "profile_coverage_pct": round(100 * profile_rows / text_rows, 1) if text_rows else 0.0,
        "code_mixed_rows": int(totals["code_mixed_rows"] or 0),
        "unsupported_rows": int(totals["unsupported_rows"] or 0),
        "translation_rows": int(totals["translation_rows"] or 0),
        "translated_rows": translated_rows,
        "translation_coverage_pct": round(100 * translated_rows / profile_rows, 1) if profile_rows else 0.0,
        "failed_translation_rows": int(totals["failed_translation_rows"] or 0),
        "skipped_translation_rows": int(totals["skipped_translation_rows"] or 0),
        "languages": [dict(row) for row in languages],
        "failures": [dict(row) for row in failures],
    }
