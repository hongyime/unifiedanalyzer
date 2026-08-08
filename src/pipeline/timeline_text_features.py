from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any, Mapping

from src.db.connection import get_analyzer_pool
from src.pipeline.text_normalizer import (
    build_canonical_timeline_text,
    source_fingerprint,
)


logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def build_feature_record(row: Mapping[str, Any], *, max_chars: int = 8000) -> dict[str, Any] | None:
    event = dict(row)
    normalized = build_canonical_timeline_text(event, max_chars=max_chars)
    if not normalized.canonical_text:
        return None
    return {
        "event_id": str(event["id"]),
        "entity_id": str(event["entity_id"]) if event.get("entity_id") else None,
        "occurred_at": event["occurred_at"],
        "source": event["source"],
        "event_type": event["event_type"],
        "source_record_id": event["source_record_id"],
        "source_fingerprint": source_fingerprint(event),
        "text_sha1": normalized.text_sha1,
        "canonical_text": normalized.canonical_text,
        "selected_metadata": normalized.selected_metadata,
        "token_count": normalized.token_count,
        "char_count": normalized.char_count,
        "emoji_count": normalized.emoji_count,
        "mention_count": normalized.mention_count,
        "hashtag_count": normalized.hashtag_count,
        "url_count": normalized.url_count,
        "domain_count": normalized.domain_count,
        "flags": normalized.flags,
        "method_versions": normalized.method_versions,
    }


async def build_timeline_text_features(batch_size: int = 500, max_events: int | None = None) -> dict:
    if max_events is None:
        max_events = _env_int("TEXT_FEATURES_BATCH_PER_RUN", 5000)

    include_unattributed = _env_bool("TEXT_FEATURES_INCLUDE_UNATTRIBUTED", False)
    max_chars = _env_int("TEXT_FEATURES_MAX_CHARS", 8000)
    refresh_recent_days = _env_int("TEXT_FEATURES_REFRESH_RECENT_DAYS", 0)

    pool = get_analyzer_pool()
    processed = 0
    written = 0
    skipped_empty = 0
    skipped_unchanged = 0
    batches = 0
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: {
        "processed": 0,
        "inserted": 0,
        "attributed": 0,
        "skipped_count": 0,
        "skipped_unchanged": 0,
    })
    remaining = max_events

    while remaining > 0:
        this_batch = min(batch_size, remaining)
        async with pool.acquire() as conn:
            candidate_rows = await conn.fetch(
                """
                SELECT emb.event_id::text AS event_id,
                       emb.occurred_at,
                       ttf.source_fingerprint AS existing_source_fingerprint,
                       ttf.event_id IS NOT NULL AS already_featured
                FROM timeline_embeddings emb
                LEFT JOIN timeline_text_features ttf ON ttf.event_id = emb.event_id
                WHERE ($2::boolean OR emb.entity_id IS NOT NULL)
                  AND (
                    ttf.event_id IS NULL
                    OR (
                      $3::int > 0
                      AND emb.occurred_at >= NOW() - ($3::int * INTERVAL '1 day')
                    )
                  )
                ORDER BY already_featured, emb.occurred_at DESC
                LIMIT $1
                """,
                this_batch,
                include_unattributed,
                refresh_recent_days,
            )
        if not candidate_rows:
            break

        event_ids = [str(row["event_id"]) for row in candidate_rows]
        occurred_at = [row["occurred_at"] for row in candidate_rows]
        existing_fingerprints = [row.get("existing_source_fingerprint") for row in candidate_rows]
        already_featured = [bool(row.get("already_featured")) for row in candidate_rows]
        lower_bound = min(occurred_at)
        upper_bound = max(occurred_at)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH candidates AS (
                    SELECT *
                    FROM UNNEST(
                        $1::uuid[],
                        $2::timestamptz[],
                        $3::text[],
                        $4::boolean[]
                    ) AS c(
                        event_id,
                        occurred_at,
                        existing_source_fingerprint,
                        already_featured
                    )
                )
                SELECT te.id::text AS id,
                       te.entity_id::text AS entity_id,
                       te.occurred_at,
                       te.source,
                       te.event_type,
                       te.source_record_id,
                       te.title,
                       te.detail,
                       te.metadata,
                       candidates.existing_source_fingerprint
                FROM candidates
                JOIN timeline_events te
                  ON te.id = candidates.event_id
                 AND te.occurred_at = candidates.occurred_at
                WHERE te.occurred_at >= $5
                  AND te.occurred_at <= $6
                  AND (
                    NULLIF(BTRIM(COALESCE(te.title, '')), '') IS NOT NULL
                    OR NULLIF(BTRIM(COALESCE(te.detail, '')), '') IS NOT NULL
                    OR COALESCE(te.metadata, '{}'::jsonb) <> '{}'::jsonb
                  )
                ORDER BY candidates.already_featured, candidates.occurred_at DESC
                """,
                event_ids,
                occurred_at,
                existing_fingerprints,
                already_featured,
                lower_bound,
                upper_bound,
            )

        upsert_rows = []
        for raw in rows:
            row = dict(raw)
            source = row.get("source") or "unknown"
            by_source[source]["processed"] += 1
            if row.get("entity_id"):
                by_source[source]["attributed"] += 1

            feature = build_feature_record(row, max_chars=max_chars)
            processed += 1
            if feature is None:
                skipped_empty += 1
                by_source[source]["skipped_count"] += 1
                continue
            if row.get("existing_source_fingerprint") == feature["source_fingerprint"]:
                skipped_unchanged += 1
                by_source[source]["skipped_unchanged"] += 1
                by_source[source]["skipped_count"] += 1
                continue

            upsert_rows.append((
                feature["event_id"],
                feature["entity_id"],
                feature["occurred_at"],
                feature["source"],
                feature["event_type"],
                feature["source_record_id"],
                feature["source_fingerprint"],
                feature["text_sha1"],
                feature["canonical_text"],
                json.dumps(feature["selected_metadata"], default=str),
                feature["token_count"],
                feature["char_count"],
                feature["emoji_count"],
                feature["mention_count"],
                feature["hashtag_count"],
                feature["url_count"],
                feature["domain_count"],
                json.dumps(feature["flags"], default=str),
                json.dumps(feature["method_versions"], default=str),
            ))
            by_source[source]["inserted"] += 1

        if upsert_rows:
            async with pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO timeline_text_features (
                        event_id, entity_id, occurred_at, source, event_type,
                        source_record_id, source_fingerprint, text_sha1,
                        canonical_text, selected_metadata, token_count,
                        char_count, emoji_count, mention_count, hashtag_count,
                        url_count, domain_count, flags, method_versions,
                        search_vector
                    )
                    VALUES (
                        $1::uuid, $2::uuid, $3, $4, $5,
                        $6, $7, $8,
                        $9, $10::jsonb, $11,
                        $12, $13, $14, $15,
                        $16, $17, $18::jsonb, $19::jsonb,
                        to_tsvector('simple', $9)
                    )
                    ON CONFLICT (event_id) DO UPDATE SET
                        entity_id = EXCLUDED.entity_id,
                        occurred_at = EXCLUDED.occurred_at,
                        source = EXCLUDED.source,
                        event_type = EXCLUDED.event_type,
                        source_record_id = EXCLUDED.source_record_id,
                        source_fingerprint = EXCLUDED.source_fingerprint,
                        text_sha1 = EXCLUDED.text_sha1,
                        canonical_text = EXCLUDED.canonical_text,
                        selected_metadata = EXCLUDED.selected_metadata,
                        token_count = EXCLUDED.token_count,
                        char_count = EXCLUDED.char_count,
                        emoji_count = EXCLUDED.emoji_count,
                        mention_count = EXCLUDED.mention_count,
                        hashtag_count = EXCLUDED.hashtag_count,
                        url_count = EXCLUDED.url_count,
                        domain_count = EXCLUDED.domain_count,
                        flags = EXCLUDED.flags,
                        method_versions = EXCLUDED.method_versions,
                        search_vector = EXCLUDED.search_vector,
                        processed_at = NOW(),
                        updated_at = NOW()
                    WHERE timeline_text_features.source_fingerprint <> EXCLUDED.source_fingerprint
                       OR timeline_text_features.entity_id IS DISTINCT FROM EXCLUDED.entity_id
                    """,
                    upsert_rows,
                )
            written += len(upsert_rows)

        batches += 1
        remaining -= len(candidate_rows)
        logger.info(
            "lexical_nlp: batch %d candidates=%d scanned=%d written=%d skipped_empty=%d skipped_unchanged=%d",
            batches,
            len(candidate_rows),
            len(rows),
            len(upsert_rows),
            skipped_empty,
            skipped_unchanged,
        )

    stats = {
        "processed": processed,
        "inserted": written,
        "skipped_count": skipped_empty + skipped_unchanged,
        "skipped_empty_text": skipped_empty,
        "skipped_unchanged": skipped_unchanged,
        "batches": batches,
        "max_events": max_events,
        "max_chars": max_chars,
        "include_unattributed": include_unattributed,
        "candidate_source": "timeline_embeddings",
        "by_source": dict(by_source),
    }
    logger.info("lexical_nlp: %s", stats)
    return stats
