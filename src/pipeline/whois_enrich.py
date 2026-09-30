"""WHOIS enrichment for domains that show up in collector.discovered_links.

For each unique registrable domain we haven't cached yet, hit Whoxy (or a
future python-whois fallback) once and store the result. Emit
identity_signals when the WHOIS owner_email matches any email already in
the analyzer's entity graph.

Source: Griffin (@hatless1der) "Beyond WHOIS" (2023-09-25) + "Art of
Pivoting" (2022-04-27). Registrable-domain owners often expose emails
that reveal identity long after the public site did.

**Default disabled** (WHOIS_ENRICH_ENABLED=0) - operator flips on after
Whoxy API key is configured. Code + migration wire cleanly regardless.

Ref: Z:\\...\\research\\spec-do-now-6-whois-enrich.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Iterable, Optional
from urllib.parse import urlparse

import httpx

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

_ENABLED = "WHOIS_ENRICH_ENABLED"
_BATCH = int(os.getenv("WHOIS_ENRICH_BATCH", "1"))     # start conservative
_QPS = float(os.getenv("WHOIS_ENRICH_QPS", "0.5"))
_PROVIDER = os.getenv("WHOIS_PROVIDER", "whoxy").lower()
_WHOXY_API_KEY = os.getenv("WHOXY_API_KEY", "")
_TIMEOUT = float(os.getenv("WHOIS_TIMEOUT_SECONDS", "10"))

# Minimal built-in "effective TLD" list for the ~99% case. tldextract or
# publicsuffix2 would be better but neither is in requirements.txt yet;
# this suffices for the initial Do Now landing and can be swapped for a
# proper library on the next dependency-refresh sweep.
_TWO_LEVEL_TLDS = {
    "co.uk", "co.jp", "co.kr", "co.in", "co.id", "co.nz", "co.za",
    "com.sg", "com.my", "com.au", "com.br", "com.tr", "com.mx",
    "com.tw", "com.hk", "com.cn", "com.ar",
    "net.au", "net.sg", "org.uk", "gov.uk", "ac.uk",
    "org.sg", "gov.sg", "edu.sg", "edu.au", "gov.au",
}


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "0") == "1"


def _registrable_domain(url: str) -> Optional[str]:
    """Best-effort registrable-domain extraction from a URL."""
    if not url:
        return None
    try:
        p = urlparse(url if "://" in url else f"http://{url}")
    except Exception:
        return None
    host = (p.hostname or "").lower().strip(".")
    if not host or "." not in host:
        return None
    # Strip trailing dot, IPv4/IPv6 quick-reject.
    if host.replace(".", "").isdigit() or ":" in host:
        return None
    parts = host.split(".")
    if len(parts) < 2:
        return None
    last_two = ".".join(parts[-2:])
    if last_two in _TWO_LEVEL_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last_two


class WhoisResult:
    """Provider-neutral WHOIS payload matching whois_cache column shape."""
    __slots__ = ("domain", "owner_name", "owner_email", "owner_organization",
                 "registrar", "registered_at", "expires_at", "privacy_flag",
                 "ns_hosts", "raw_payload", "source", "error")

    def __init__(self, domain: str, *, error: str | None = None, **kw) -> None:
        self.domain = domain
        self.owner_name = kw.get("owner_name")
        self.owner_email = kw.get("owner_email")
        self.owner_organization = kw.get("owner_organization")
        self.registrar = kw.get("registrar")
        self.registered_at = kw.get("registered_at")
        self.expires_at = kw.get("expires_at")
        self.privacy_flag = bool(kw.get("privacy_flag", False))
        self.ns_hosts = kw.get("ns_hosts") or None
        self.raw_payload = kw.get("raw_payload")
        self.source = kw.get("source", _PROVIDER)
        self.error = error


def _looks_privacy_protected(payload: dict) -> bool:
    """Heuristic: WHOIS record uses a privacy-proxy service."""
    text_bits = [
        (payload.get("registrant_contact") or {}).get("full_name", ""),
        (payload.get("registrant_contact") or {}).get("email_address", ""),
        (payload.get("registrant_contact") or {}).get("company_name", ""),
    ]
    joined = " ".join(str(b or "") for b in text_bits).lower()
    return any(marker in joined for marker in (
        "privacy", "whoisguard", "proxy", "redacted", "gdpr",
        "domainsbyproxy", "contactprivacy", "withheld"
    ))


async def _whoxy_fetch(client: httpx.AsyncClient, domain: str) -> WhoisResult:
    if not _WHOXY_API_KEY:
        return WhoisResult(domain, error="whoxy_api_key_missing")
    url = f"https://api.whoxy.com/?key={_WHOXY_API_KEY}&whois={domain}"
    try:
        r = await client.get(url, timeout=_TIMEOUT)
    except Exception as exc:
        return WhoisResult(domain, error=f"http:{type(exc).__name__}")
    if r.status_code == 429:
        return WhoisResult(domain, error="rate_limited")
    if r.status_code != 200:
        return WhoisResult(domain, error=f"http:{r.status_code}")
    try:
        payload = r.json()
    except Exception:
        return WhoisResult(domain, error="bad_json")
    if payload.get("status") == 0:
        # Whoxy uses status=0 to mean lookup failed; often it's NXDOMAIN.
        return WhoisResult(domain, raw_payload=payload, error=payload.get("status_reason") or "no_record")

    reg = payload.get("registrant_contact") or {}
    ns = payload.get("name_servers") or []
    privacy = _looks_privacy_protected(payload)
    return WhoisResult(
        domain,
        owner_name=None if privacy else reg.get("full_name"),
        owner_email=None if privacy else reg.get("email_address"),
        owner_organization=None if privacy else reg.get("company_name"),
        registrar=payload.get("domain_registrar", {}).get("registrar_name") if isinstance(payload.get("domain_registrar"), dict) else None,
        registered_at=payload.get("create_date"),
        expires_at=payload.get("expiry_date"),
        privacy_flag=privacy,
        ns_hosts=[str(n) for n in ns] if ns else None,
        raw_payload=payload,
    )


async def _fetch_domain(client: httpx.AsyncClient, domain: str) -> WhoisResult:
    if _PROVIDER == "whoxy":
        return await _whoxy_fetch(client, domain)
    return WhoisResult(domain, error=f"unknown_provider:{_PROVIDER}")


async def _emit_owner_email_signals(analyzer, collector, whois: WhoisResult) -> int:
    """When whois.owner_email matches an email already known to the analyzer,
    emit a whois_owner_email signal. Returns count emitted."""
    if not whois.owner_email or whois.privacy_flag:
        return 0
    normalized = whois.owner_email.strip().lower()
    if "@" not in normalized:
        return 0
    async with analyzer.acquire() as acon:
        # Emails live in identity_signals with signal_type in ('commit_email',
        # 'email_match'), value=email. There is NO entity_emails table.
        rows = await acon.fetch(
            """\
            SELECT DISTINCT entity_id::text AS entity_id
            FROM identity_signals
            WHERE signal_type IN ('commit_email','email_match')
              AND lower(value) = $1
              AND entity_id IS NOT NULL
            LIMIT 20
            """,
            normalized,
        )
    if not rows:
        return 0

    signal_rows = [(
        row["entity_id"],
        "whois_owner_email",
        "whois",
        "whois_cache",
        "owner_email",
        whois.domain,
        "entity",
        row["entity_id"],
        json.dumps({"domain": whois.domain, "owner_email": normalized}),
        0.9,
    ) for row in rows]

    async with analyzer.acquire() as acon:
        await acon.executemany(
            """
            INSERT INTO identity_signals
                (entity_id, signal_type, source_platform, source_table, source_column,
                 source_record_id, target_platform, target_record_id, value, confidence)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT DO NOTHING
            """,
            signal_rows,
        )
    return len(signal_rows)


async def run_whois_enrich() -> dict:
    summary = {"skipped": None, "domains_probed": 0, "cache_hits": 0,
               "errors": 0, "signals_emitted": 0}
    if not _is_enabled():
        summary["skipped"] = "disabled"
        return summary
    if _PROVIDER == "whoxy" and not _WHOXY_API_KEY:
        summary["skipped"] = "whoxy_api_key_missing"
        return summary

    try:
        collector = get_collector_pool()
    except Exception:
        summary["skipped"] = "no_collector_pool"
        return summary
    analyzer = get_analyzer_pool()

    # Phase A: candidate domains from recent discovered_links.
    async with collector.acquire() as ccon:
        rows = await ccon.fetch(
            """
            SELECT DISTINCT url
            FROM discovered_links
            WHERE url IS NOT NULL
              AND (discovered_at > now() - interval '48 hours' OR status = 'pending')
            LIMIT 500
            """
        )

    seen: set[str] = set()
    candidates: list[str] = []
    for r in rows:
        rd = _registrable_domain(r["url"])
        if rd and rd not in seen:
            seen.add(rd)
            candidates.append(rd)

    if not candidates:
        return summary

    # Skip domains we already have in whois_cache.
    async with collector.acquire() as ccon:
        cached_rows = await ccon.fetch(
            "SELECT domain FROM whois_cache WHERE domain = ANY($1::text[])",
            candidates,
        )
    cached = {r["domain"] for r in cached_rows}
    summary["cache_hits"] = len(cached)
    todo = [d for d in candidates if d not in cached][:_BATCH]

    if not todo:
        return summary

    interval = 1.0 / max(_QPS, 0.01)
    async with httpx.AsyncClient() as client:
        for domain in todo:
            result = await _fetch_domain(client, domain)
            summary["domains_probed"] += 1

            if result.error and result.error != "no_record":
                summary["errors"] += 1
                if result.error == "rate_limited":
                    logger.warning("whois_enrich: rate-limited, bailing cycle")
                    break
                await asyncio.sleep(interval)
                continue

            # UPSERT into whois_cache (lives on the collector DB).
            async with collector.acquire() as ccon:
                await ccon.execute(
                    """
                    INSERT INTO whois_cache (
                        domain, owner_name, owner_email, owner_organization,
                        registrar, registered_at, expires_at, privacy_flag,
                        ns_hosts, raw_payload, source, fetched_at
                    ) VALUES ($1, $2, $3, $4, $5,
                              CASE WHEN $6::text IS NULL THEN NULL ELSE $6::text::timestamptz END,
                              CASE WHEN $7::text IS NULL THEN NULL ELSE $7::text::timestamptz END,
                              $8, $9, $10, $11, now())
                    ON CONFLICT (domain) DO UPDATE SET
                        owner_name = EXCLUDED.owner_name,
                        owner_email = EXCLUDED.owner_email,
                        owner_organization = EXCLUDED.owner_organization,
                        registrar = EXCLUDED.registrar,
                        registered_at = EXCLUDED.registered_at,
                        expires_at = EXCLUDED.expires_at,
                        privacy_flag = EXCLUDED.privacy_flag,
                        ns_hosts = EXCLUDED.ns_hosts,
                        raw_payload = EXCLUDED.raw_payload,
                        source = EXCLUDED.source,
                        fetched_at = now()
                    """,
                    result.domain,
                    result.owner_name,
                    result.owner_email,
                    result.owner_organization,
                    result.registrar,
                    str(result.registered_at) if result.registered_at else None,
                    str(result.expires_at) if result.expires_at else None,
                    result.privacy_flag,
                    result.ns_hosts,
                    json.dumps(result.raw_payload) if result.raw_payload else None,
                    result.source,
                )

            emitted = await _emit_owner_email_signals(analyzer, collector, result)
            summary["signals_emitted"] += emitted
            await asyncio.sleep(interval)

    logger.info("whois_enrich: %s", summary)
    return summary


__all__ = ["run_whois_enrich", "_registrable_domain"]
