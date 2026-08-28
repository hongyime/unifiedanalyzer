"""CTI enrichment (plan task CTI1): cross-reference our normalized_indicators
against free, no-auth threat-intel IOC feeds and flag known-malicious hits.

Feeds (operator decision: enrichment + alerts) are read-only and free:
abuse.ch URLhaus + Feodo Tracker, and OpenPhish. This module is the PURE,
fully-unit-tested core: feed parsing + indicator matching. The DB side
(writing threat_context into normalized_indicators.metadata and emitting
alerts) is wired at deploy time (operator-present) since it touches live
schemas + the scheduler loop; keeping the core pure makes it testable and
safe to land now with zero live-system risk.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

# Free, no-auth IOC feeds (plaintext, one entry per line, '#' comments).
DEFAULT_FEEDS: dict[str, dict[str, str]] = {
    "urlhaus": {"url": "https://urlhaus.abuse.ch/downloads/text/", "kind": "url"},
    "feodo": {"url": "https://feodotracker.abuse.ch/downloads/ipblocklist.txt", "kind": "ipv4"},
    "openphish": {"url": "https://openphish.com/feed.txt", "kind": "url"},
}

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


@dataclass(frozen=True)
class IOC:
    value: str  # normalized host: bare domain or ip
    kind: str   # 'domain' | 'ipv4'
    feed: str


def _host_of(token: str) -> str | None:
    token = token.strip()
    if not token:
        return None
    if "://" in token:
        try:
            host = urlparse(token).hostname
        except Exception:
            return None
        return host.lower() if host else None
    # bare host / ip / host-with-path
    host = token.split("/", 1)[0].split(":", 1)[0].strip().lower()
    return host or None


def _classify(host: str) -> str | None:
    try:
        ipaddress.ip_address(host)
        return "ipv4" if _IPV4_RE.match(host) else "ip6"
    except ValueError:
        if "." in host and " " not in host:
            return "domain"
    return None


def parse_ioc_feed(text: str, feed: str) -> list[IOC]:
    """Parse a plaintext IOC feed (URL list or IP blocklist) into deduped IOCs."""
    out: list[IOC] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        host = _host_of(line)
        if not host:
            continue
        kind = _classify(host)
        if kind not in {"domain", "ipv4"}:
            continue  # skip ipv6 / junk for the compact indicator model
        if host in seen:
            continue
        seen.add(host)
        out.append(IOC(value=host, kind=kind, feed=feed))
    return out


def build_ioc_index(iocs: Iterable[IOC]) -> dict[str, IOC]:
    """Index IOCs by normalized value (last feed wins on collision)."""
    return {i.value: i for i in iocs}


def match_indicators(
    indicators: Iterable[tuple[str, str]],
    ioc_index: dict[str, IOC],
) -> list[dict]:
    """Match our (indicator_type, normalized_value) rows against the IOC index.

    Domains/IPs match on value; emails match on their domain part. Returns a
    list of malicious matches suitable for tagging + alerting.
    """
    matches: list[dict] = []
    for itype, value in indicators:
        v = (value or "").strip().lower()
        t = (itype or "").strip().lower()
        if not v:
            continue
        cand: IOC | None = None
        if t in {"domain", "ipv4", "ip"}:
            cand = ioc_index.get(v)
        elif t == "email" and "@" in v:
            cand = ioc_index.get(v.split("@", 1)[1])
        elif t in {"url"}:
            host = _host_of(v)
            cand = ioc_index.get(host) if host else None
        if cand is not None:
            matches.append({
                "indicator_type": itype,
                "normalized_value": value,
                "matched_value": cand.value,
                "feed": cand.feed,
                "threat": "known_malicious",
            })
    return matches


async def fetch_feeds(feeds: dict[str, dict[str, str]] | None = None, *, timeout: float = 20.0) -> list[IOC]:
    """Fetch + parse configured feeds. Thin network layer (not unit-tested).

    Fail-soft per feed: a single feed error never aborts the others.
    """
    import httpx

    feeds = feeds or DEFAULT_FEEDS
    all_iocs: list[IOC] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for name, spec in feeds.items():
            try:
                resp = await client.get(spec["url"])
                resp.raise_for_status()
                all_iocs.extend(parse_ioc_feed(resp.text, name))
            except Exception:  # noqa: BLE001 - one bad feed must not sink enrichment
                continue
    return all_iocs
