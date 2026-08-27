"""Normalized indicator extraction and compact Supabase export staging."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import asyncpg


EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])")
DOMAIN_RE = re.compile(r"\b((?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63})\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{6,}\d)(?!\d)")
USERNAME_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9._-]{2,80})")
QUOTED_FULL_NAME_RE = re.compile(r'"([A-Z][A-Za-z\' -]{1,80}\s+[A-Z][A-Za-z\' -]{1,80})"')
SUPABASE_REMOTE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS normalized_indicators (
    id UUID PRIMARY KEY,
    indicator_type VARCHAR(30) NOT NULL,
    normalized_value TEXT NOT NULL,
    display_value TEXT,
    source_families TEXT[] NOT NULL DEFAULT '{}',
    evidence_count INT NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    first_seen_at TIMESTAMP WITH TIME ZONE,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB NOT NULL DEFAULT '{}',
    exported_from TEXT NOT NULL DEFAULT 'unifiedanalyzer',
    local_created_at TIMESTAMP WITH TIME ZONE,
    local_updated_at TIMESTAMP WITH TIME ZONE,
    exported_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(indicator_type, normalized_value)
);
CREATE INDEX IF NOT EXISTS idx_normalized_indicators_type_last_seen
    ON normalized_indicators(indicator_type, last_seen_at DESC);
ALTER TABLE normalized_indicators ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE normalized_indicators FROM anon, authenticated;
"""

SUPABASE_REMOTE_UPSERT_SQL = """
INSERT INTO normalized_indicators (
    id, indicator_type, normalized_value, display_value, source_families,
    evidence_count, confidence, first_seen_at, last_seen_at, metadata,
    exported_from, local_created_at, local_updated_at, exported_at
)
VALUES (
    $1, $2, $3, $4, $5::text[], $6, $7, $8, $9, $10::jsonb,
    'unifiedanalyzer', $11, $12, NOW()
)
ON CONFLICT (indicator_type, normalized_value) DO UPDATE SET
    display_value = COALESCE(EXCLUDED.display_value, normalized_indicators.display_value),
    source_families = (
        SELECT ARRAY(
            SELECT DISTINCT unnest(normalized_indicators.source_families || EXCLUDED.source_families)
            ORDER BY 1
        )
    ),
    evidence_count = GREATEST(normalized_indicators.evidence_count, EXCLUDED.evidence_count),
    confidence = GREATEST(normalized_indicators.confidence, EXCLUDED.confidence),
    first_seen_at = LEAST(
        COALESCE(normalized_indicators.first_seen_at, EXCLUDED.first_seen_at),
        COALESCE(EXCLUDED.first_seen_at, normalized_indicators.first_seen_at)
    ),
    last_seen_at = GREATEST(
        COALESCE(normalized_indicators.last_seen_at, EXCLUDED.last_seen_at),
        COALESCE(EXCLUDED.last_seen_at, normalized_indicators.last_seen_at)
    ),
    metadata = normalized_indicators.metadata || EXCLUDED.metadata,
    local_updated_at = EXCLUDED.local_updated_at,
    exported_at = NOW()
"""


@dataclass(frozen=True)
class NormalizedIndicator:
    indicator_type: str
    normalized_value: str
    display_value: str
    confidence: float = 0.7
    metadata: dict[str, Any] | None = None


def _valid_ipv4(value: str) -> str | None:
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError:
        return None


def _normalize_domain(value: str) -> str | None:
    domain = (value or "").strip().lower().strip(".")
    if not domain or "." not in domain:
        return None
    try:
        domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = domain.split(".")
    if any(not label or len(label) > 63 for label in labels):
        return None
    if all(part.isdigit() for part in labels):
        return None
    return domain


def _normalize_email(value: str) -> str | None:
    email = (value or "").strip().lower()
    if EMAIL_RE.fullmatch(email):
        return email
    return None


def normalize_phone_e164(value: str, *, default_region: str | None = None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return None
    raw = (value or "").strip()
    if raw.startswith("+"):
        candidate = "+" + digits
    else:
        region = (default_region or os.getenv("ANALYZER_DEFAULT_PHONE_REGION", "US")).upper()
        if region == "SG" and len(digits) == 8 and digits[0] in {"6", "8", "9"}:
            candidate = "+65" + digits
        elif region == "US" and len(digits) == 10:
            candidate = "+1" + digits
        elif len(digits) >= 11 and len(digits) <= 15:
            candidate = "+" + digits
        else:
            return None
    if 8 <= len(candidate) <= 16:
        return candidate
    return None


def normalize_indicator(
    indicator_type: str,
    value: str,
    *,
    default_region: str | None = None,
) -> NormalizedIndicator | None:
    itype = (indicator_type or "").strip().lower()
    raw = (value or "").strip()
    if not raw:
        return None
    if itype == "domain":
        normalized = _normalize_domain(raw)
    elif itype in {"ip", "ipv4"}:
        normalized = _valid_ipv4(raw)
        itype = "ipv4"
    elif itype == "email":
        normalized = _normalize_email(raw)
    elif itype in {"phone", "phone_e164"}:
        normalized = normalize_phone_e164(raw, default_region=default_region)
        itype = "phone_e164"
    elif itype == "username":
        normalized = raw.lstrip("@").lower()
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,80}", normalized):
            normalized = None
    elif itype in {"full_name", "name"}:
        normalized = re.sub(r"\s+", " ", raw.strip('" ')).strip()
        itype = "full_name"
        if " " not in normalized or len(normalized) > 120:
            normalized = None
    else:
        normalized = None
    if not normalized:
        return None
    return NormalizedIndicator(
        indicator_type=itype,
        normalized_value=normalized,
        display_value=normalized if itype != "full_name" else normalized.title(),
        confidence=0.8 if itype in {"email", "phone_e164", "ipv4"} else 0.7,
        metadata={"extractor": "analyzer_indicator_export"},
    )


def extract_indicators_from_text(text: str | None, *, default_region: str | None = None) -> list[NormalizedIndicator]:
    if not text:
        return []
    found: dict[tuple[str, str], NormalizedIndicator] = {}
    for regex, indicator_type in (
        (EMAIL_RE, "email"),
        (IPV4_RE, "ipv4"),
        (PHONE_RE, "phone_e164"),
        (USERNAME_RE, "username"),
        (QUOTED_FULL_NAME_RE, "full_name"),
    ):
        for match in regex.finditer(text):
            value = match.group(1) if match.groups() else match.group(0)
            indicator = normalize_indicator(indicator_type, value, default_region=default_region)
            if indicator:
                found[(indicator.indicator_type, indicator.normalized_value)] = indicator
    email_domains = {item.normalized_value.rsplit("@", 1)[-1] for item in found.values() if item.indicator_type == "email"}
    for match in DOMAIN_RE.finditer(text):
        value = match.group(1)
        if value.lower() in email_domains:
            continue
        indicator = normalize_indicator("domain", value, default_region=default_region)
        if indicator:
            found[(indicator.indicator_type, indicator.normalized_value)] = indicator
    return list(found.values())


def resolve_domain_to_ips(domain: str, *, resolver=None) -> list[str]:
    normalized = _normalize_domain(domain)
    if not normalized:
        return []
    resolver = resolver or socket.getaddrinfo
    try:
        rows = resolver(normalized, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return []
    ips = {
        ip
        for row in rows
        if len(row) >= 5
        for ip in [_valid_ipv4(str(row[4][0]))]
        if ip
    }
    return sorted(ips)


async def upsert_normalized_indicators(
    conn,
    indicators: Iterable[NormalizedIndicator],
    *,
    source_family: str,
    evidence_ref: dict[str, Any] | None = None,
    exportable_min_confidence: float = 0.75,
) -> int:
    rows = list(indicators)
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO normalized_indicators (
            indicator_type, normalized_value, display_value, source_families,
            evidence_count, confidence, metadata, supabase_exportable, updated_at
        )
        VALUES ($1, $2, $3, ARRAY[$4]::text[], 1, $5, $6::jsonb, $7, NOW())
        ON CONFLICT (indicator_type, normalized_value) DO UPDATE SET
            display_value = COALESCE(EXCLUDED.display_value, normalized_indicators.display_value),
            source_families = (
                SELECT ARRAY(
                    SELECT DISTINCT unnest(normalized_indicators.source_families || EXCLUDED.source_families)
                    ORDER BY 1
                )
            ),
            evidence_count = normalized_indicators.evidence_count + 1,
            confidence = GREATEST(normalized_indicators.confidence, EXCLUDED.confidence),
            metadata = normalized_indicators.metadata || EXCLUDED.metadata,
            supabase_exportable = normalized_indicators.supabase_exportable OR EXCLUDED.supabase_exportable,
            export_status = CASE
                WHEN normalized_indicators.export_status = 'exported'
                     AND (normalized_indicators.metadata IS DISTINCT FROM normalized_indicators.metadata || EXCLUDED.metadata
                          OR NOT normalized_indicators.source_families @> EXCLUDED.source_families)
                    THEN 'pending'
                ELSE normalized_indicators.export_status
            END,
            updated_at = NOW(),
            last_seen_at = NOW()
        """,
        [
            (
                row.indicator_type,
                row.normalized_value,
                row.display_value,
                source_family,
                row.confidence,
                json.dumps({**(row.metadata or {}), "evidence_ref": evidence_ref or {}}, default=str),
                row.confidence >= exportable_min_confidence,
            )
            for row in rows
        ],
    )
    return len(rows)


def supabase_export_config() -> dict[str, Any]:
    url_configured = bool(os.getenv("SUPABASE_URL"))
    service_role_configured = bool(os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    secret_key_configured = bool(os.getenv("SUPABASE_SECRET_KEY"))
    database_url_configured = bool(os.getenv("SUPABASE_DATABASE_URL") or os.getenv("SUPABASE_DB_URL"))
    publishable_configured = bool(os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY"))
    if database_url_configured:
        write_method = "postgres_direct"
    elif service_role_configured or secret_key_configured:
        write_method = "data_api_secret"
    else:
        write_method = "not_configured"
    return {
        "configured": url_configured and write_method != "not_configured",
        "url_configured": url_configured,
        "project_id_configured": bool(os.getenv("SUPABASE_PROJECT_ID")),
        "publishable_configured": publishable_configured,
        "service_role_configured": service_role_configured,
        "secret_key_configured": secret_key_configured,
        "database_url_configured": database_url_configured,
        "write_method": write_method,
        "mode": os.getenv("SUPABASE_INDICATOR_EXPORT_MODE", "disabled"),
        "free_tier_db_budget_mb": 500,
        "payload": "normalized_indicators_only",
    }


def _supabase_mode(mode: str | None = None) -> str:
    return (mode or os.getenv("SUPABASE_INDICATOR_EXPORT_MODE", "disabled")).strip().lower()


def _supabase_database_url(database_url: str | None = None) -> str | None:
    return database_url or os.getenv("SUPABASE_DATABASE_URL") or os.getenv("SUPABASE_DB_URL")


def _export_params(row: Any) -> tuple:
    return (
        uuid.UUID(str(row["id"])),
        row["indicator_type"],
        row["normalized_value"],
        row["display_value"],
        list(row["source_families"] or []),
        int(row["evidence_count"] or 0),
        float(row["confidence"] or 0),
        row["first_seen_at"],
        row["last_seen_at"],
        json.dumps(row["metadata"] or {}, default=str),
        row["created_at"],
        row["updated_at"],
    )


async def _mark_exported(conn, ids: list[uuid.UUID], *, write_method: str) -> None:
    if not ids:
        return
    await conn.execute(
        """
        UPDATE normalized_indicators
        SET export_status = 'exported',
            exported_at = NOW(),
            updated_at = NOW(),
            metadata = metadata || jsonb_build_object(
                'supabase_export',
                jsonb_build_object('write_method', $2::text, 'exported_at', NOW())
            )
        WHERE id = ANY($1::uuid[])
        """,
        ids,
        write_method,
    )


async def _mark_export_retry(conn, ids: list[uuid.UUID], error: str) -> None:
    if not ids:
        return
    await conn.execute(
        """
        UPDATE normalized_indicators
        SET export_status = 'retry',
            updated_at = NOW(),
            metadata = metadata || jsonb_build_object(
                'supabase_export_error',
                jsonb_build_object('message', LEFT($2::text, 500), 'at', NOW())
            )
        WHERE id = ANY($1::uuid[])
        """,
        ids,
        error,
    )


async def export_pending_supabase_indicators(
    conn,
    *,
    limit: int = 100,
    dry_run: bool = False,
    mode: str | None = None,
    ensure_schema: bool = True,
    ensure_schema_when_empty: bool = False,
    database_url: str | None = None,
    remote_conn=None,
) -> dict[str, Any]:
    """Export compact normalized indicators to Supabase via direct Postgres."""
    mode_value = _supabase_mode(mode)
    capped = max(1, min(int(limit or 100), 1000))
    rows = await conn.fetch(
        """
        SELECT id::text, indicator_type, normalized_value, display_value,
               source_families, evidence_count, confidence,
               first_seen_at, last_seen_at, metadata, created_at, updated_at
        FROM normalized_indicators
        WHERE supabase_exportable = TRUE
          AND export_status IN ('pending', 'retry')
        ORDER BY updated_at ASC
        LIMIT $1
        """,
        capped,
    )
    ids = [uuid.UUID(str(row["id"])) for row in rows]
    result: dict[str, Any] = {
        "status": "dry_run" if dry_run else "ok",
        "mode": mode_value,
        "write_method": "postgres_direct",
        "selected": len(rows),
        "exported": 0,
        "raw_mirror": False,
        "payload": "normalized_indicators_only",
    }
    if dry_run or mode_value in {"dry-run", "dry_run", "preview"}:
        result["status"] = "dry_run"
        return result
    if mode_value in {"", "disabled", "off", "0", "false"}:
        result["status"] = "skipped"
        result["reason"] = "mode_disabled"
        return result
    if mode_value not in {"postgres_direct", "direct", "enabled", "write", "on", "1", "true"}:
        result["status"] = "skipped"
        result["reason"] = f"unsupported_mode:{mode_value}"
        return result
    if not rows and not (ensure_schema and ensure_schema_when_empty):
        return result

    dsn = _supabase_database_url(database_url)
    owns_remote = remote_conn is None
    remote = remote_conn
    try:
        if remote is None:
            if not dsn:
                raise RuntimeError("SUPABASE_DATABASE_URL is not configured")
            remote = await asyncpg.connect(dsn, timeout=float(os.getenv("SUPABASE_CONNECT_TIMEOUT_SECONDS", "15")))
        if ensure_schema:
            await remote.execute(SUPABASE_REMOTE_TABLE_SQL)
            result["schema_ensured"] = True
        if not rows:
            return result
        await remote.executemany(SUPABASE_REMOTE_UPSERT_SQL, [_export_params(row) for row in rows])
        await _mark_exported(conn, ids, write_method="postgres_direct")
    except Exception as exc:  # noqa: BLE001 - exporter must report and leave retry state
        await _mark_export_retry(conn, ids, str(exc))
        result["status"] = "error"
        result["error"] = str(exc)[:500]
        return result
    finally:
        if owns_remote and remote is not None:
            await remote.close()

    result["exported"] = len(rows)
    return result


async def reconcile_supabase_indicators(
    conn,
    *,
    clean: bool = False,
    mode: str | None = None,
    database_url: str | None = None,
    remote_conn=None,
) -> dict[str, Any]:
    """Reconcile the remote Supabase mirror against local exported truth.

    Ticket T14 (operator decision: reconcile+clean). Reports remote rows that
    have no matching locally-exported (indicator_type, normalized_value); with
    clean=True, deletes those orphans so Supabase provably mirrors local truth.
    """
    mode_value = _supabase_mode(mode)
    result: dict[str, Any] = {
        "status": "ok",
        "mode": mode_value,
        "clean": bool(clean),
        "local_exported": 0,
        "remote_rows": 0,
        "orphans": 0,
        "deleted": 0,
    }
    if mode_value not in {"postgres_direct", "direct", "enabled", "write", "on", "1", "true"}:
        result["status"] = "skipped"
        result["reason"] = f"unsupported_mode:{mode_value}"
        return result
    dsn = _supabase_database_url(database_url)
    owns_remote = remote_conn is None
    remote = remote_conn
    try:
        if remote is None:
            if not dsn:
                raise RuntimeError("SUPABASE_DATABASE_URL is not configured")
            remote = await asyncpg.connect(dsn, timeout=float(os.getenv("SUPABASE_CONNECT_TIMEOUT_SECONDS", "15")))
        local_rows = await conn.fetch(
            """
            SELECT indicator_type, normalized_value
            FROM normalized_indicators
            WHERE export_status = 'exported'
            """
        )
        local_set = {(r["indicator_type"], r["normalized_value"]) for r in local_rows}
        remote_rows = await remote.fetch(
            "SELECT indicator_type, normalized_value FROM normalized_indicators"
        )
        remote_set = {(r["indicator_type"], r["normalized_value"]) for r in remote_rows}
        result["local_exported"] = len(local_set)
        result["remote_rows"] = len(remote_set)
        orphans = sorted(remote_set - local_set)
        result["orphans"] = len(orphans)
        result["orphan_samples"] = [
            {"indicator_type": t, "normalized_value": v} for t, v in orphans[:20]
        ]
        if clean and orphans:
            deleted = 0
            for i in range(0, len(orphans), 500):
                chunk = orphans[i:i + 500]
                types = [t for t, _ in chunk]
                values = [v for _, v in chunk]
                res = await remote.execute(
                    """
                    DELETE FROM normalized_indicators ni
                    USING unnest($1::text[], $2::text[]) AS o(indicator_type, normalized_value)
                    WHERE ni.indicator_type = o.indicator_type
                      AND ni.normalized_value = o.normalized_value
                    """,
                    types,
                    values,
                )
                try:
                    deleted += int(res.split()[-1])
                except (IndexError, ValueError):
                    pass
            result["deleted"] = deleted
    except Exception as exc:  # noqa: BLE001 - reconcile must report, not crash
        result["status"] = "error"
        result["error"] = str(exc)[:500]
        return result
    finally:
        if owns_remote and remote is not None:
            await remote.close()
    return result
