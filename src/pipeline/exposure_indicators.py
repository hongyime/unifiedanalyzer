"""Stage Collector exposure findings as compact Analyzer indicators.

Collector owns broad exposure discovery. Analyzer owns normalized intelligence
and Supabase export. This bridge intentionally stores only compact indicators
and redacted evidence metadata; it does not mirror raw snippets, queries, or
page contents into Analyzer/Supabase.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.pipeline.indicator_export import (
    NormalizedIndicator,
    extract_indicators_from_text,
    normalize_indicator,
)

OFFSET_SOURCE = "exposure_indicators"
OFFSET_TABLE = "exposure_findings"


@dataclass(frozen=True)
class ExposureIndicatorReport:
    scanned: int = 0
    staged: int = 0
    unique_indicators: int = 0
    skipped_no_indicator: int = 0
    newest_collected_at: Any = None
    newest_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "staged": self.staged,
            "unique_indicators": self.unique_indicators,
            "skipped_no_indicator": self.skipped_no_indicator,
            "newest_collected_at": (
                self.newest_collected_at.isoformat()
                if hasattr(self.newest_collected_at, "isoformat")
                else self.newest_collected_at
            ),
            "newest_id": self.newest_id,
        }


async def stage_exposure_findings_as_indicators(
    analyzer_conn,
    collector_conn,
    *,
    limit: int = 1000,
    default_region: str | None = None,
) -> dict[str, Any]:
    """Read new Collector exposure findings and stage normalized indicators."""
    capped = max(1, min(int(limit or 1000), 10_000))
    await _ensure_offset_table(analyzer_conn)
    last_seen_at, last_seen_id = await _last_cursor(analyzer_conn)
    rows = await _fetch_exposure_rows(collector_conn, last_seen_at, last_seen_id, capped)
    if not rows:
        return ExposureIndicatorReport().to_dict()

    staged = 0
    skipped = 0
    newest = None
    newest_id = None
    indicator_rows: list[NormalizedIndicator] = []
    for row in rows:
        indicators = _indicators_for_exposure_row(row, default_region=default_region)
        if indicators:
            indicator_rows.extend(_with_evidence_ref(item, row) for item in indicators)
            staged += len(indicators)
        else:
            skipped += 1
        collected_at = _row_get(row, "collected_at")
        row_id = str(_row_get(row, "id") or "")
        if collected_at is not None and (
            newest is None or (collected_at, row_id) >= (newest, newest_id or "")
        ):
            newest = collected_at
            newest_id = row_id

    unique_indicators = await _upsert_exposure_indicators(analyzer_conn, indicator_rows)
    if newest is not None:
        await _save_cursor(analyzer_conn, newest, newest_id)
    return ExposureIndicatorReport(
        scanned=len(rows),
        staged=staged,
        unique_indicators=unique_indicators,
        skipped_no_indicator=skipped,
        newest_collected_at=newest,
        newest_id=newest_id,
    ).to_dict()


async def _upsert_exposure_indicators(conn, indicators: list[NormalizedIndicator]) -> int:
    if not indicators:
        return 0
    rows = _collapse_indicators(indicators)
    await conn.executemany(
        """
        INSERT INTO normalized_indicators (
            indicator_type, normalized_value, display_value, source_families,
            evidence_count, confidence, metadata, supabase_exportable, updated_at
        )
        VALUES ($1, $2, $3, ARRAY['exposure']::text[], $4, $5, $6::jsonb, $7, NOW())
        ON CONFLICT (indicator_type, normalized_value) DO UPDATE SET
            display_value = COALESCE(EXCLUDED.display_value, normalized_indicators.display_value),
            source_families = (
                SELECT ARRAY(
                    SELECT DISTINCT unnest(normalized_indicators.source_families || EXCLUDED.source_families)
                    ORDER BY 1
                )
            ),
            evidence_count = normalized_indicators.evidence_count + EXCLUDED.evidence_count,
            confidence = GREATEST(normalized_indicators.confidence, EXCLUDED.confidence),
            metadata = normalized_indicators.metadata || EXCLUDED.metadata,
            supabase_exportable = normalized_indicators.supabase_exportable OR EXCLUDED.supabase_exportable,
            export_status = CASE
                WHEN normalized_indicators.export_status = 'exported' THEN 'pending'
                ELSE normalized_indicators.export_status
            END,
            updated_at = NOW(),
            last_seen_at = NOW()
        """,
        rows,
    )
    return len(rows)


def _collapse_indicators(indicators: list[NormalizedIndicator]) -> list[tuple[Any, ...]]:
    collapsed: dict[tuple[str, str], dict[str, Any]] = {}
    for item in indicators:
        key = (item.indicator_type, item.normalized_value)
        entry = collapsed.setdefault(
            key,
            {
                "indicator_type": item.indicator_type,
                "normalized_value": item.normalized_value,
                "display_value": item.display_value,
                "confidence": item.confidence,
                "metadata": {},
                "evidence_count": 0,
                "supabase_exportable": False,
            },
        )
        entry["evidence_count"] += 1
        entry["confidence"] = max(float(entry["confidence"] or 0), float(item.confidence or 0))
        entry["supabase_exportable"] = bool(entry["supabase_exportable"]) or item.confidence >= 0.75
        entry["metadata"] = {**entry["metadata"], **(item.metadata or {})}

    # Ticket T12 (operator decision 2026-08-26, "redact + export"): low-confidence
    # exposure indicators become exportable once reduced to a non-identifying
    # form: emails -> hashed local part, IPv4 -> /24 network, phones -> hashed
    # subscriber suffix. Domains are already low-PII and pass through as-is.
    if os.getenv("ANALYZER_EXPOSURE_REDACTED_EXPORT", "1").strip().lower() not in {"0", "false", "no"}:
        for entry in collapsed.values():
            if entry["supabase_exportable"]:
                continue
            itype = str(entry["indicator_type"] or "").lower()
            value = str(entry["normalized_value"] or "")
            try:
                if itype == "email" and "@" in value:
                    local_part, _, domain = value.partition("@")
                    value = hashlib.sha256(local_part.encode()).hexdigest()[:12] + "@" + domain
                elif itype in {"ipv4", "ip"} and value.count(".") == 3:
                    a, b, c, _d = value.split(".")
                    value = f"{a}.{b}.{c}.0/24"
                elif itype == "phone" and len(value) >= 8:
                    value = value[:3] + "-h" + hashlib.sha256(value.encode()).hexdigest()[:8]
                elif itype != "domain":
                    continue
                entry["normalized_value"] = value
                entry["display_value"] = value
                entry["supabase_exportable"] = True
            except Exception:
                continue

    return [
        (
            entry["indicator_type"],
            entry["normalized_value"],
            entry["display_value"],
            int(entry["evidence_count"]),
            float(entry["confidence"]),
            json.dumps(entry["metadata"] or {}, default=str, sort_keys=True),
            bool(entry["supabase_exportable"]),
        )
        for entry in collapsed.values()
    ]


def _with_evidence_ref(indicator: NormalizedIndicator, row: Any) -> NormalizedIndicator:
    return NormalizedIndicator(
        indicator_type=indicator.indicator_type,
        normalized_value=indicator.normalized_value,
        display_value=indicator.display_value,
        confidence=indicator.confidence,
        metadata={
            **(indicator.metadata or {}),
            "evidence_ref": _redacted_evidence_ref(row),
        },
    )


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _indicators_for_exposure_row(
    row: Any,
    *,
    default_region: str | None = None,
) -> list[NormalizedIndicator]:
    confidence = _exposure_confidence(row)
    values: dict[tuple[str, str], NormalizedIndicator] = {}

    domain = _normal_domain(_row_get(row, "domain"))
    if not domain:
        domain = _host_from_url(_row_get(row, "url"))
    if domain:
        item = normalize_indicator("domain", domain)
        if item:
            values[(item.indicator_type, item.normalized_value)] = _with_exposure_metadata(
                item,
                row,
                confidence=confidence,
            )

    text = " ".join(
        str(part or "")
        for part in (
            _row_get(row, "title"),
            _row_get(row, "snippet"),
            _row_get(row, "url"),
        )
    )
    for item in extract_indicators_from_text(text, default_region=default_region):
        if item.indicator_type not in {"domain", "email", "ipv4"}:
            continue
        values[(item.indicator_type, item.normalized_value)] = _with_exposure_metadata(
            item,
            row,
            confidence=max(confidence, item.confidence),
        )
    return list(values.values())


def _with_exposure_metadata(
    indicator: NormalizedIndicator,
    row: Any,
    *,
    confidence: float,
) -> NormalizedIndicator:
    return NormalizedIndicator(
        indicator_type=indicator.indicator_type,
        normalized_value=indicator.normalized_value,
        display_value=indicator.display_value,
        confidence=max(indicator.confidence, confidence),
        metadata={
            **(indicator.metadata or {}),
            "extractor": "collector_exposure_findings",
            "exposure": {
                "category": _row_get(row, "category"),
                "severity": _row_get(row, "severity"),
                "detected_secret": bool(_row_get(row, "detected_secret") or False),
                "url_hash": _row_get(row, "url_hash") or _hash_value(_row_get(row, "url")),
            },
        },
    )


def _exposure_confidence(row: Any) -> float:
    severity = str(_row_get(row, "severity") or "").lower()
    severity_floor = {
        "critical": 0.9,
        "high": 0.8,
        "medium": 0.7,
        "low": 0.55,
    }.get(severity, 0.5)
    try:
        raw = float(_row_get(row, "confidence") or 0)
    except (TypeError, ValueError):
        raw = 0.0
    return max(severity_floor, raw)


def _normal_domain(value: Any) -> str | None:
    text = str(value or "").strip().lower().strip(".")
    if not text or "." not in text:
        return None
    return text


def _host_from_url(value: Any) -> str | None:
    try:
        parsed = urlparse(str(value or ""))
    except Exception:
        return None
    return _normal_domain(parsed.hostname)


def _hash_value(value: Any) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _redacted_evidence_ref(row: Any) -> dict[str, Any]:
    return {
        "table": "collector.exposure_findings",
        "id": str(_row_get(row, "id") or ""),
        "category": _row_get(row, "category"),
        "severity": _row_get(row, "severity"),
        "domain_hash": _hash_value(_row_get(row, "domain")),
        "url_hash": _row_get(row, "url_hash") or _hash_value(_row_get(row, "url")),
    }


async def _ensure_offset_table(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stream_alert_offsets (
            source_name    TEXT NOT NULL,
            cursor_table   TEXT NOT NULL,
            cursor_value   TEXT,
            last_seen_at   TIMESTAMPTZ,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (source_name, cursor_table)
        )
        """
    )


async def _last_cursor(conn) -> tuple[Any, str | None]:
    row = await conn.fetchrow(
        """
        SELECT last_seen_at, cursor_value
        FROM stream_alert_offsets
        WHERE source_name = $1 AND cursor_table = $2
        """,
        OFFSET_SOURCE,
        OFFSET_TABLE,
    )
    if not row:
        return None, None
    last_seen_at = _row_get(row, "last_seen_at")
    cursor_value = _row_get(row, "cursor_value")
    if cursor_value is None:
        return last_seen_at, None
    cursor_text = str(cursor_value)
    if hasattr(last_seen_at, "isoformat") and cursor_text == last_seen_at.isoformat():
        return last_seen_at, None
    return last_seen_at, cursor_text


async def _save_cursor(conn, value: Any, row_id: str | None) -> None:
    await conn.execute(
        """
        INSERT INTO stream_alert_offsets (source_name, cursor_table, cursor_value, last_seen_at, updated_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (source_name, cursor_table) DO UPDATE SET
            cursor_value = EXCLUDED.cursor_value,
            last_seen_at = EXCLUDED.last_seen_at,
            updated_at = NOW()
        """,
        OFFSET_SOURCE,
        OFFSET_TABLE,
        row_id or (value.isoformat() if hasattr(value, "isoformat") else str(value)),
        value,
    )


async def _fetch_exposure_rows(collector_conn, last_seen_at: Any, last_seen_id: str | None, limit: int):
    exists = await collector_conn.fetchval("SELECT to_regclass('public.exposure_findings') IS NOT NULL")
    if not exists:
        return []
    if last_seen_at is None:
        return await collector_conn.fetch(
            """
            SELECT id::text, target_scope, query, url, domain, category, severity,
                   confidence, title, snippet, detected_secret, metadata,
                   collected_at, url_hash
            FROM exposure_findings
            ORDER BY collected_at ASC, id::text ASC
            LIMIT $1
            """,
            limit,
        )
    if last_seen_id:
        return await collector_conn.fetch(
            """
            SELECT id::text, target_scope, query, url, domain, category, severity,
                   confidence, title, snippet, detected_secret, metadata,
                   collected_at, url_hash
            FROM exposure_findings
            WHERE collected_at > $1
               OR (collected_at = $1 AND id::text > $2)
            ORDER BY collected_at ASC, id::text ASC
            LIMIT $3
            """,
            last_seen_at,
            last_seen_id,
            limit,
        )
    return await collector_conn.fetch(
        """
        SELECT id::text, target_scope, query, url, domain, category, severity,
               confidence, title, snippet, detected_secret, metadata,
               collected_at, url_hash
        FROM exposure_findings
        WHERE collected_at > $1
        ORDER BY collected_at ASC, id::text ASC
        LIMIT $2
        """,
        last_seen_at,
        limit,
    )
