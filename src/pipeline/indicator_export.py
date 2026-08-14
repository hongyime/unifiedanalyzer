"""Normalized indicator extraction and compact Supabase export staging."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from typing import Any, Iterable


EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])")
DOMAIN_RE = re.compile(r"\b((?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63})\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{6,}\d)(?!\d)")
USERNAME_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9._-]{2,80})")
QUOTED_FULL_NAME_RE = re.compile(r'"([A-Z][A-Za-z\' -]{1,80}\s+[A-Z][A-Za-z\' -]{1,80})"')


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
    if service_role_configured or secret_key_configured:
        write_method = "data_api_secret"
    elif database_url_configured:
        write_method = "postgres_direct"
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
