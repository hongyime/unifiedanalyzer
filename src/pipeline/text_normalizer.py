from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse


NORMALIZER_VERSION = "timeline-text-v1"

_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_MENTION_RE = re.compile(r"(?<![\w.])@[\w][\w._-]{1,63}", re.UNICODE)
_HASHTAG_RE = re.compile(r"(?<![\w])#[\w][\w._-]{1,80}", re.UNICODE)
_TOKEN_RE = re.compile(r"[@#]?[\w][\w._'/-]*", re.UNICODE)

_TEXT_METADATA_KEYS = (
    "caption",
    "text",
    "message_preview",
    "target_preview",
    "quoted_text",
    "location_name",
    "venue_title",
    "venue_address",
    "owner_username",
    "author_username",
    "target_username",
    "username",
    "emoji",
    "source_url",
    "url",
    "domain",
    "hashtags",
    "mentions",
)


@dataclass(frozen=True)
class NormalizedTimelineText:
    canonical_text: str
    text_sha1: str
    selected_metadata: dict[str, Any]
    token_count: int
    char_count: int
    emoji_count: int
    mention_count: int
    hashtag_count: int
    url_count: int
    domain_count: int
    flags: dict[str, Any]
    method_versions: dict[str, str]


def text_sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def source_fingerprint(row: Mapping[str, Any]) -> str:
    metadata = _coerce_json(row.get("metadata"))
    stable = {
        "event_id": _clean(row.get("id") or row.get("event_id")),
        "entity_id": _clean(row.get("entity_id")),
        "occurred_at": _clean(row.get("occurred_at")),
        "source": _clean(row.get("source")),
        "event_type": _clean(row.get("event_type")),
        "source_record_id": _clean(row.get("source_record_id")),
        "title": _clean(row.get("title")),
        "detail": _clean(row.get("detail")),
        "metadata": metadata,
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_canonical_timeline_text(
    row: Mapping[str, Any],
    *,
    max_chars: int = 8000,
) -> NormalizedTimelineText:
    metadata = _coerce_json(row.get("metadata"))
    selected = _select_metadata(metadata)
    parts: list[str] = []
    _append_text(parts, row.get("title"))
    _append_text(parts, row.get("detail"))
    for value in selected.values():
        _append_metadata_text(parts, value)

    raw_text = "\n".join(parts)
    canonical = normalize_social_text(raw_text, max_chars=max_chars)
    urls = _URL_RE.findall(canonical)
    domains = sorted({domain for url in urls if (domain := _domain_from_url(url))})
    flags = {
        "empty_text": not bool(canonical),
        "truncated": len(normalize_social_text(raw_text, max_chars=0)) > max_chars if max_chars > 0 else False,
    }
    if domains:
        selected.setdefault("domains", domains)

    return NormalizedTimelineText(
        canonical_text=canonical,
        text_sha1=text_sha1(canonical),
        selected_metadata=selected,
        token_count=len(_TOKEN_RE.findall(canonical)),
        char_count=len(canonical),
        emoji_count=sum(1 for ch in canonical if _is_emoji(ch)),
        mention_count=len(_MENTION_RE.findall(canonical)),
        hashtag_count=len(_HASHTAG_RE.findall(canonical)),
        url_count=len(urls),
        domain_count=len(domains),
        flags={key: value for key, value in flags.items() if value},
        method_versions={"text_normalizer": NORMALIZER_VERSION},
    )


def normalize_social_text(text: str | None, *, max_chars: int = 8000) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = "\n".join(line.strip() for line in normalized.split("\n"))
    normalized = normalized.strip()
    if max_chars > 0 and len(normalized) > max_chars:
        return normalized[:max_chars].rstrip()
    return normalized


def _coerce_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {"raw": value.decode("utf-8", "replace") if isinstance(value, bytes) else value}
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    return {"raw": value}


def _select_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for key in _TEXT_METADATA_KEYS:
        value = metadata.get(key)
        if value in (None, "", [], {}):
            continue
        selected[key] = value
    return selected


def _append_text(parts: list[str], value: Any) -> None:
    if value in (None, ""):
        return
    text = str(value).strip()
    if text:
        parts.append(text)


def _append_metadata_text(parts: list[str], value: Any) -> None:
    if value in (None, "", [], {}):
        return
    if isinstance(value, str):
        _append_text(parts, value)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _append_metadata_text(parts, item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _append_metadata_text(parts, item)
        return
    _append_text(parts, value)


def _domain_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _is_emoji(ch: str) -> bool:
    code = ord(ch)
    return (
        0x1F000 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
        or 0xFE00 <= code <= 0xFE0F
    )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
