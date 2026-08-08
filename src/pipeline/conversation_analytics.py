from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


def _decode(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
    return {}


def _thread_key(row: Any) -> str:
    meta = _decode(row["metadata"])
    target = row["target_entity_id"] or "unknown"
    if row["source"] == "telegram":
        msg = str(meta.get("target_message_id") or meta.get("reply_to_message_id") or row["source_record_id"])
        chat = msg.rsplit(":", 1)[0] if ":" in msg else "direct"
        day = row["occurred_at"].date().isoformat() if row["occurred_at"] else "unknown"
        return f"telegram:{chat}:{row['actor_entity_id']}:{target}:{day}"
    day = row["occurred_at"].date().isoformat() if row["occurred_at"] else "unknown"
    return f"{row['source']}:{row['actor_entity_id']}:{target}:{day}"


async def build_conversation_analytics(batch_size: int = 5000, max_interactions: int | None = None) -> dict:
    max_interactions = max_interactions or int(os.getenv("CONVERSATION_ANALYTICS_MAX_INTERACTIONS", "25000"))
    limit = min(batch_size, max_interactions)
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT actor_entity_id::text, target_entity_id::text, interaction_type,
                   source, source_record_id, occurred_at, metadata
            FROM entity_interactions
            WHERE source = 'telegram'
              AND actor_entity_id IS NOT NULL
              AND occurred_at IS NOT NULL
            ORDER BY occurred_at DESC
            LIMIT $1
            """,
            limit,
        )

    threads: dict[str, dict[str, Any]] = {}
    participant_counts: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {
        "message_count": 0,
        "reply_count": 0,
        "reaction_count": 0,
    })
    for row in rows:
        key = _thread_key(row)
        meta = _decode(row["metadata"])
        thread = threads.setdefault(key, {
            "thread_id": key,
            "source": row["source"],
            "entity_id": row["actor_entity_id"],
            "peer_entity_id": row["target_entity_id"],
            "title": key.split(":", 2)[-1],
            "started_at": row["occurred_at"],
            "last_message_at": row["occurred_at"],
            "message_count": 0,
            "reply_count": 0,
            "reaction_count": 0,
            "forwarded_count": 0,
            "preview": [],
        })
        thread["message_count"] += 1
        if row["interaction_type"] == "replied":
            thread["reply_count"] += 1
        if row["interaction_type"] == "reacted":
            thread["reaction_count"] += 1
        if row["interaction_type"] == "forwarded":
            thread["forwarded_count"] += 1
        if row["occurred_at"] and row["occurred_at"] < thread["started_at"]:
            thread["started_at"] = row["occurred_at"]
        if row["occurred_at"] and row["occurred_at"] > thread["last_message_at"]:
            thread["last_message_at"] = row["occurred_at"]
        if len(thread["preview"]) < 5:
            thread["preview"].append({
                "source_record_id": row["source_record_id"],
                "interaction_type": row["interaction_type"],
                "occurred_at": _iso(row["occurred_at"]),
                "text": (meta.get("message_preview") or meta.get("target_preview") or "")[:240],
            })

        participant = participant_counts[(key, row["actor_entity_id"])]
        participant["message_count"] += 1
        if row["interaction_type"] == "replied":
            participant["reply_count"] += 1
        if row["interaction_type"] == "reacted":
            participant["reaction_count"] += 1

    thread_rows = [
        (
            t["thread_id"], t["source"], t["entity_id"], t["peer_entity_id"], t["title"],
            t["started_at"], t["last_message_at"], t["message_count"], t["reply_count"],
            t["reaction_count"], t["forwarded_count"], None, json.dumps({"context_only": True}),
            json.dumps(t["preview"], default=str),
        )
        for t in threads.values()
    ]
    metric_rows = [
        (
            thread_id, entity_id, "telegram", counts["message_count"], counts["reply_count"],
            counts["reaction_count"], None, json.dumps({"context_only": True}),
        )
        for (thread_id, entity_id), counts in participant_counts.items()
    ]

    async with pool.acquire() as conn:
        if thread_rows:
            await conn.executemany(
                """
                INSERT INTO conversation_threads (
                    thread_id, source, entity_id, peer_entity_id, title, started_at,
                    last_message_at, message_count, reply_count, reaction_count,
                    forwarded_count, avg_response_seconds, sentiment_summary, preview
                )
                VALUES ($1, $2, $3::uuid, $4::uuid, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14::jsonb)
                ON CONFLICT (thread_id) DO UPDATE SET
                    entity_id = EXCLUDED.entity_id,
                    peer_entity_id = EXCLUDED.peer_entity_id,
                    title = EXCLUDED.title,
                    started_at = EXCLUDED.started_at,
                    last_message_at = EXCLUDED.last_message_at,
                    message_count = EXCLUDED.message_count,
                    reply_count = EXCLUDED.reply_count,
                    reaction_count = EXCLUDED.reaction_count,
                    forwarded_count = EXCLUDED.forwarded_count,
                    avg_response_seconds = EXCLUDED.avg_response_seconds,
                    sentiment_summary = EXCLUDED.sentiment_summary,
                    preview = EXCLUDED.preview,
                    updated_at = NOW()
                """,
                thread_rows,
            )
        if metric_rows:
            await conn.executemany(
                """
                INSERT INTO conversation_participant_metrics (
                    thread_id, entity_id, source, message_count, reply_count,
                    reaction_count, avg_response_seconds, sentiment_summary
                )
                VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb)
                ON CONFLICT (thread_id, entity_id) DO UPDATE SET
                    message_count = EXCLUDED.message_count,
                    reply_count = EXCLUDED.reply_count,
                    reaction_count = EXCLUDED.reaction_count,
                    avg_response_seconds = EXCLUDED.avg_response_seconds,
                    sentiment_summary = EXCLUDED.sentiment_summary,
                    updated_at = NOW()
                """,
                metric_rows,
            )

    stats = {"processed": len(rows), "threads": len(thread_rows), "participant_metrics": len(metric_rows)}
    logger.info("conversation_analytics: %s", stats)
    return stats
