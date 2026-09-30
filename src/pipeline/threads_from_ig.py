"""IG -> Threads username probe.

For each instagram_profiles.username, HEAD https://www.threads.net/@<username>
and record whether a Threads account exists, plus its public bio and avatar.

Source: Griffin (@hatless1der) "Threads OSINT Secrets" (2024-10-10):
threads.net/@<ig_username> resolves even when IG doesn't link to it;
Threads bio + photo can differ from IG; follower/following lists are
public by default even when the linked IG is private.

Read-only. HEAD/GET only. No cookies attached. Realtime-blocking
sources are irrelevant here (this is a periodic batch enricher).

Ref: Z:\\...\\research\\spec-do-now-4-threads-from-ig.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from typing import Optional

import httpx

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

_ENABLED = "THREADS_PROBE_ENABLED"
_BATCH = int(os.getenv("THREADS_PROBE_BATCH", "50"))
_QPS = float(os.getenv("THREADS_PROBE_QPS", "1"))
_STALE_DAYS = int(os.getenv("THREADS_PROBE_STALE_DAYS", "30"))
_TIMEOUT = float(os.getenv("THREADS_PROBE_TIMEOUT_SECONDS", "5"))

_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
]

# Threads embeds bio + avatar inside a JSON blob in <script> tags.
# We're deliberately permissive — capture what we can, tolerate shape changes.
_BIO_RE = re.compile(r'"biography"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)
_AVATAR_RE = re.compile(r'"profile_pic_url_hd"\s*:\s*"((?:[^"\\]|\\.)*)"')
_FOLLOWER_RE = re.compile(r'"follower_count"\s*:\s*(\d+)')
_VERIFIED_RE = re.compile(r'"is_verified"\s*:\s*(true|false)')


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "1") == "1"


def _decode_json_str(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s


async def _probe_one(client: httpx.AsyncClient, username: str) -> dict:
    """Probe a single username. Returns dict shaped for UPDATE:
    {exists: bool | None, bio: str | None, avatar_url: str | None,
     follower_count: int | None, is_verified: bool | None, error: str | None}.
    exists=None means the probe itself failed (timeout / 429 / network).
    exists=False means Threads returned 404.
    exists=True means the page exists (bio/avatar may still be None if private).
    """
    url = f"https://www.threads.net/@{username}"
    try:
        head = await client.head(url, timeout=_TIMEOUT, follow_redirects=True)
    except Exception as exc:
        return {"exists": None, "error": f"head:{type(exc).__name__}"}

    if head.status_code == 404:
        return {"exists": False, "bio": None, "avatar_url": None, "follower_count": None, "is_verified": None, "error": None}
    if head.status_code == 429:
        return {"exists": None, "error": "rate_limited"}
    if head.status_code >= 400:
        return {"exists": None, "error": f"http:{head.status_code}"}

    try:
        page = await client.get(url, timeout=_TIMEOUT, follow_redirects=True)
    except Exception as exc:
        return {"exists": None, "error": f"get:{type(exc).__name__}"}

    if page.status_code != 200:
        return {"exists": None, "error": f"http:{page.status_code}"}

    html = page.text
    bio_match = _BIO_RE.search(html)
    avatar_match = _AVATAR_RE.search(html)
    follower_match = _FOLLOWER_RE.search(html)
    verified_match = _VERIFIED_RE.search(html)
    return {
        "exists": True,
        "bio": _decode_json_str(bio_match.group(1)) if bio_match else None,
        "avatar_url": _decode_json_str(avatar_match.group(1)) if avatar_match else None,
        "follower_count": int(follower_match.group(1)) if follower_match else None,
        "is_verified": (verified_match.group(1) == "true") if verified_match else None,
        "error": None,
    }


async def run_threads_from_ig() -> dict:
    """Pipeline phase entrypoint. Returns a small summary dict for logging."""
    summary = {"probed": 0, "exists_true": 0, "exists_false": 0, "errors": 0, "signals_emitted": 0}
    if not _is_enabled():
        summary["skipped"] = "disabled"
        return summary

    try:
        collector_pool = get_collector_pool()
    except Exception:
        summary["skipped"] = "no_collector_pool"
        return summary
    analyzer_pool = get_analyzer_pool()

    # Pick a batch: never-probed first, then stalest.
    async with collector_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT platform_user_id, username
            FROM instagram_profiles
            WHERE username IS NOT NULL
              AND (threads_probe_at IS NULL
                   OR threads_probe_at < now() - ($1::int || ' days')::interval)
            ORDER BY threads_probe_at NULLS FIRST
            LIMIT $2
            """,
            _STALE_DAYS,
            _BATCH,
        )

    if not rows:
        return summary

    ua = random.choice(_UA_POOL)
    headers = {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}
    interval = 1.0 / max(_QPS, 0.01)

    async with httpx.AsyncClient(headers=headers, http2=False) as client:
        for row in rows:
            username = row["username"]
            result = await _probe_one(client, username)
            summary["probed"] += 1

            if result["exists"] is None:
                summary["errors"] += 1
                # Rate-limited: bail this cycle to be polite. Everything else: log + move on.
                if result.get("error") == "rate_limited":
                    logger.warning("threads_from_ig: rate-limited on %s, bailing cycle", username)
                    break
                logger.info("threads_from_ig: skip %s (%s)", username, result.get("error"))
                await asyncio.sleep(interval)
                continue

            # UPDATE instagram_profiles regardless of exists true/false
            # (both are valid probe outcomes worth caching).
            async with collector_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE instagram_profiles
                    SET threads_probe_at = now(),
                        threads_probe_exists = $1,
                        threads_probe_bio = $2,
                        threads_probe_avatar_url = $3
                    WHERE platform_user_id = $4
                    """,
                    result["exists"],
                    result.get("bio"),
                    result.get("avatar_url"),
                    row["platform_user_id"],
                )

            if result["exists"]:
                summary["exists_true"] += 1
                # Emit an identity_signals row: IG↔Threads username pair.
                # Confidence 0.9 (URL exists, self-published pairing).
                async with analyzer_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO identity_signals (
                            entity_id, signal_type,
                            source_platform, source_table, source_column, source_record_id,
                            target_platform, target_record_id,
                            value, confidence
                        ) VALUES (NULL, 'threads_ig_username_pair',
                                  'instagram', 'instagram_profiles', 'username', $1,
                                  'threads', $2,
                                  $3, 0.9)
                        ON CONFLICT DO NOTHING
                        """,
                        row["platform_user_id"],
                        username,
                        json.dumps({"bio_preview": (result.get("bio") or "")[:120]}),
                    )
                summary["signals_emitted"] += 1
            else:
                summary["exists_false"] += 1

            await asyncio.sleep(interval)

    logger.info("threads_from_ig: %s", summary)
    return summary


__all__ = ["run_threads_from_ig"]
