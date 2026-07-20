from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

HINT_TYPE_SAME_PERSON_95_99 = "same_person_probability_95_99"
MIN_HINT_CONFIDENCE = 0.95
MAX_HINT_CONFIDENCE_EXCLUSIVE = 1.0
DEFAULT_HINT_PRIORITY = 1

TARGETABLE_SOURCES = {
    "github",
    "instagram",
    "strava",
    "telegram",
    "threads",
    "tiktok",
    "whatsapp",
    "x",
    "youtube",
}


@dataclass(frozen=True)
class CollectorPriorityHint:
    source: str
    target_id: str
    target_username: str | None
    priority: int
    confidence: float
    hint_type: str
    entity_id: str
    candidate_entity_id: str
    relationship_id: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target_id": self.target_id,
            "target_username": self.target_username,
            "priority": self.priority,
            "confidence": self.confidence,
            "hint_type": self.hint_type,
            "entity_id": self.entity_id,
            "candidate_entity_id": self.candidate_entity_id,
            "relationship_id": self.relationship_id,
            "evidence": self.evidence,
            "status": self.status,
        }

    def as_record(self) -> tuple[Any, ...]:
        return (
            self.source,
            self.target_id,
            self.target_username,
            self.priority,
            self.confidence,
            self.hint_type,
            self.entity_id,
            self.candidate_entity_id,
            self.relationship_id,
            json.dumps(self.evidence, default=str, sort_keys=True),
            self.status,
        )


@dataclass
class CollectorPriorityHintReport:
    dry_run: bool
    planned: int
    written: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    hints: list[CollectorPriorityHint] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "planned": self.planned,
            "written": self.written,
            "skipped": dict(sorted(self.skipped.items())),
            "hints": [hint.to_dict() for hint in self.hints],
        }

    def to_text(self) -> str:
        mode = "dry-run" if self.dry_run else "write"
        lines = [
            f"Collector priority hints ({mode})",
            f"Planned: {self.planned}",
            f"Written: {self.written}",
        ]
        if self.skipped:
            lines.append("Skipped:")
            lines.extend(f"  {reason}: {count}" for reason, count in sorted(self.skipped.items()))
        if self.hints:
            lines.append("Hints:")
            for hint in self.hints[:50]:
                username = f" @{hint.target_username}" if hint.target_username else ""
                lines.append(
                    "  "
                    f"{hint.source}:{hint.target_id}{username} "
                    f"confidence={hint.confidence:.4f} "
                    f"entity={hint.entity_id} candidate={hint.candidate_entity_id}"
                )
            if len(self.hints) > 50:
                lines.append(f"  ... {len(self.hints) - 50} more")
        return "\n".join(lines)


async def export_collector_priority_hints(
    conn,
    *,
    write: bool = False,
) -> CollectorPriorityHintReport:
    """Preview or persist analyzer-owned priority hints for collector.

    The exporter only stages hints in the analyzer database. It does not merge
    identities and never writes the collector database.
    """
    hints, skipped = await build_collector_priority_hints(conn)
    written = await upsert_collector_priority_hints(conn, hints) if write else 0
    report = CollectorPriorityHintReport(
        dry_run=not write,
        planned=len(hints),
        written=written,
        skipped=skipped,
        hints=hints,
    )
    logger.info(
        "collector priority hints %s: planned=%d written=%d skipped=%s",
        "written" if write else "dry-run",
        report.planned,
        report.written,
        report.skipped,
    )
    return report


async def build_collector_priority_hints(conn) -> tuple[list[CollectorPriorityHint], dict[str, int]]:
    relationships = await _fetch_candidate_relationships(conn)
    skipped: dict[str, int] = defaultdict(int)
    eligible_relationships = []
    for row in relationships:
        confidence = _confidence_from_row(row)
        if confidence is None:
            skipped["missing_confidence"] += 1
            continue
        if not (MIN_HINT_CONFIDENCE <= confidence < MAX_HINT_CONFIDENCE_EXCLUSIVE):
            skipped["confidence_outside_95_99"] += 1
            continue
        entity_a = _clean(_row_get(row, "entity_a_id"))
        entity_b = _clean(_row_get(row, "entity_b_id"))
        if not entity_a or not entity_b or entity_a == entity_b:
            skipped["invalid_relationship_entities"] += 1
            continue
        eligible_relationships.append((row, confidence, entity_a, entity_b))

    if not eligible_relationships:
        return [], dict(skipped)

    entity_ids = sorted({item[2] for item in eligible_relationships} | {item[3] for item in eligible_relationships})
    links_by_entity = await _fetch_links_by_entity(conn, entity_ids)
    hints_by_key: dict[tuple[str, str, str, str, str], CollectorPriorityHint] = {}

    for row, confidence, entity_a, entity_b in eligible_relationships:
        _add_directional_hints(hints_by_key, skipped, row, confidence, entity_a, entity_b, links_by_entity)
        _add_directional_hints(hints_by_key, skipped, row, confidence, entity_b, entity_a, links_by_entity)

    hints = sorted(
        hints_by_key.values(),
        key=lambda hint: (
            hint.priority,
            -hint.confidence,
            hint.source,
            hint.target_id,
            hint.entity_id,
            hint.candidate_entity_id,
        ),
    )
    return hints, dict(skipped)


async def upsert_collector_priority_hints(conn, hints: list[CollectorPriorityHint]) -> int:
    if not hints:
        return 0

    async def _write() -> None:
        await conn.executemany(
            """
            INSERT INTO collector_priority_hints (
                source,
                target_id,
                target_username,
                priority,
                confidence,
                hint_type,
                entity_id,
                candidate_entity_id,
                relationship_id,
                evidence,
                status,
                created_at,
                updated_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7::uuid,
                $8::uuid,
                $9::uuid,
                $10::jsonb,
                $11,
                NOW(),
                NOW()
            )
            ON CONFLICT (source, target_id, hint_type, entity_id, candidate_entity_id)
            DO UPDATE SET
                target_username = EXCLUDED.target_username,
                priority = EXCLUDED.priority,
                confidence = EXCLUDED.confidence,
                relationship_id = EXCLUDED.relationship_id,
                evidence = EXCLUDED.evidence,
                status = CASE
                    WHEN collector_priority_hints.status = 'dismissed'
                    THEN collector_priority_hints.status
                    ELSE EXCLUDED.status
                END,
                updated_at = NOW()
            """,
            [hint.as_record() for hint in hints],
        )

    if hasattr(conn, "transaction"):
        async with conn.transaction():
            await _write()
    else:
        await _write()
    return len(hints)


async def _fetch_candidate_relationships(conn):
    return await conn.fetch(
        """
        SELECT
            er.id::text AS relationship_id,
            er.entity_a_id::text AS entity_a_id,
            er.entity_b_id::text AS entity_b_id,
            er.relationship_type,
            er.weight,
            CASE
                WHEN er.weight >= 10 THEN er.weight::float / 100.0
                ELSE er.weight::float
            END AS confidence,
            er.cross_platform,
            er.sources,
            er.updated_at
        FROM entity_relationships er
        WHERE er.relationship_type = 'same_person_probability'
          AND (
                (er.weight >= 95 AND er.weight < 100)
             OR (er.weight >= 0.95 AND er.weight < 1.0)
          )
        ORDER BY er.weight DESC NULLS LAST, er.updated_at DESC NULLS LAST
        """
    )


async def _fetch_links_by_entity(conn, entity_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    rows = await conn.fetch(
        """
        SELECT
            entity_id::text AS entity_id,
            source,
            platform_id,
            platform_username,
            platform_name,
            confidence AS link_confidence,
            link_method,
            is_confirmed
        FROM entity_platform_links
        WHERE retracted_at IS NULL
          AND entity_id = ANY($1::uuid[])
        """,
        entity_ids,
    )
    links_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        entity_id = _clean(_row_get(row, "entity_id"))
        if not entity_id:
            continue
        links_by_entity[entity_id].append(
            {
                "source": _normalize_source(_row_get(row, "source")),
                "platform_id": _clean(_row_get(row, "platform_id")),
                "platform_username": _clean(_row_get(row, "platform_username")),
                "platform_name": _clean(_row_get(row, "platform_name")),
                "link_confidence": _row_get(row, "link_confidence"),
                "link_method": _clean(_row_get(row, "link_method")),
                "is_confirmed": bool(_row_get(row, "is_confirmed", False)),
            }
        )
    return dict(links_by_entity)


def _add_directional_hints(
    hints_by_key: dict[tuple[str, str, str, str, str], CollectorPriorityHint],
    skipped: dict[str, int],
    relationship: Any,
    confidence: float,
    entity_id: str,
    candidate_entity_id: str,
    links_by_entity: dict[str, list[dict[str, Any]]],
) -> None:
    base_links = links_by_entity.get(entity_id, [])
    candidate_links = links_by_entity.get(candidate_entity_id, [])
    if not base_links:
        skipped["entity_without_links"] += 1
        return
    if not candidate_links:
        skipped["candidate_without_links"] += 1
        return

    target_found = False
    for candidate_link in candidate_links:
        source = _normalize_source(candidate_link.get("source"))
        target_id = _clean(candidate_link.get("platform_id"))
        if not source or source not in TARGETABLE_SOURCES:
            skipped["non_targetable_source"] += 1
            continue
        if not target_id:
            skipped["missing_target_id"] += 1
            continue
        target_found = True
        relationship_id = _clean(_row_get(relationship, "relationship_id") or _row_get(relationship, "id"))
        evidence = {
            "exporter": "collector_priority_hints",
            "policy": "same_person_probability_95_99_no_auto_merge",
            "relationship_type": "same_person_probability",
            "relationship_weight": _row_get(relationship, "weight"),
            "relationship_sources": _json_value(_row_get(relationship, "sources")),
            "base_entity_links": [_evidence_link(link) for link in base_links],
            "candidate_link": _evidence_link(candidate_link),
        }
        hint = CollectorPriorityHint(
            source=source,
            target_id=target_id,
            target_username=_clean(candidate_link.get("platform_username")),
            priority=DEFAULT_HINT_PRIORITY,
            confidence=round(confidence, 4),
            hint_type=HINT_TYPE_SAME_PERSON_95_99,
            entity_id=entity_id,
            candidate_entity_id=candidate_entity_id,
            relationship_id=relationship_id,
            evidence=evidence,
        )
        key = (hint.source, hint.target_id, hint.hint_type, hint.entity_id, hint.candidate_entity_id)
        existing = hints_by_key.get(key)
        if existing is None or hint.confidence > existing.confidence:
            hints_by_key[key] = hint

    if not target_found:
        skipped["candidate_without_targetable_links"] += 1


def _evidence_link(link: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "source": _normalize_source(link.get("source")),
            "platform_id": _clean(link.get("platform_id")),
            "platform_username": _clean(link.get("platform_username")),
            "platform_name": _clean(link.get("platform_name")),
            "link_confidence": link.get("link_confidence"),
            "link_method": _clean(link.get("link_method")),
            "is_confirmed": link.get("is_confirmed"),
        }.items()
        if value not in (None, "", [], {})
    }


def _confidence_from_row(row: Any) -> float | None:
    raw = _row_get(row, "confidence")
    if raw is None:
        raw = _row_get(row, "weight")
    try:
        confidence = float(raw)
    except (TypeError, ValueError):
        return None
    if confidence >= 10:
        confidence = confidence / 100.0
    return confidence


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _normalize_source(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "ig": "instagram",
        "instagramgo": "instagram",
        "telegramgo": "telegram",
        "twitter": "x",
        "twitter/x": "x",
        "whatsappgo": "whatsapp",
    }
    return aliases.get(text, text.replace(" ", "_"))


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_value(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
