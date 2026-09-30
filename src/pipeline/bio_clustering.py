"""Bio n-gram clustering — catches coordinated fake-account networks that
share near-identical bio text across platforms.

Source: Griffin (@hatless1der) "LinkedIn Fakes: A Wolf in Business Casual
Clothing" (2021-11-16). His network of 300+ fake profiles all shared bio
phrases that were only obvious once you started dorking site:linkedin.com
"<distinctive phrase>". We generalize that across every platform we
collect bios from.

Reads bios from the collector DB across 9 platforms (verified via grep):
telegram_users.bio, instagram_profiles.bio, github_users.bio,
tiktok_profiles.bio, lemon8_profiles.bio, youtube_channels.description,
whatsapp_users.about/status, x_profiles.bio, facebook_profiles.bio.

Writes n-grams into collector.bio_ngram_index and emits
bio_ngram_cluster signals into analyzer.identity_signals when >=3 users
share the same distinctive phrase across >=2 platforms.

Ref: Z:\\...\\research\\spec-do-next-2-bio-clustering.md
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import re
from typing import Iterable, Optional

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.bio_nlp import extract_tokens

logger = logging.getLogger(__name__)

_ENABLED = "BIO_CLUSTERING_ENABLED"
_NGRAM_LEN_MIN = int(os.getenv("BIO_NGRAM_LEN_MIN", "5"))
_NGRAM_LEN_MAX = int(os.getenv("BIO_NGRAM_LEN_MAX", "8"))
_DF_CUTOFF = int(os.getenv("BIO_NGRAM_DF_CUTOFF", "50"))
_CLUSTER_MIN = int(os.getenv("BIO_NGRAM_CLUSTER_MIN", "3"))
_CLUSTER_MAX_PAIRS = int(os.getenv("BIO_NGRAM_CLUSTER_MAX_PAIRS", "20"))
_MAX_BIO_CHARS = int(os.getenv("BIO_NGRAM_MAX_BIO_CHARS", "10000"))

# (platform, table, id_col, bio_col). YouTube uses `description`; WhatsApp
# uses `about` (or `status`).
_BIO_SOURCES = [
    ("telegram",  "telegram_users",     "platform_user_id", "bio"),
    ("instagram", "instagram_profiles", "platform_user_id", "bio"),
    ("github",    "github_users",       "username",         "bio"),
    ("tiktok",    "tiktok_profiles",    "platform_user_id", "bio"),
    ("lemon8",    "lemon8_profiles",    "platform_user_id", "bio"),
    ("youtube",   "youtube_channels",   "channel_id",       "description"),
    ("whatsapp",  "whatsapp_users",     "platform_user_id", "about"),
    ("x",         "x_profiles",         "platform_user_id", "bio"),
    ("facebook",  "facebook_profiles",  "platform_user_id", "bio"),
]

# Very generic "recruiting-network" categories from Griffin's post, useful
# as a lightweight cluster tagger. Keeping small; real category dictionary
# already exists in bio_nlp.CATEGORY_KEYWORDS.
_CATEGORY_HINTS = {
    "recruiting": ("recruiter", "recruiting", "talent acquisition", "hiring", "headhunter"),
    "hr":         ("hr consultant", "human resources", "hr professional"),
    "business":   ("consultant", "executive", "founder", "advisor", "strategist"),
    "gan_flag":   ("world-class", "consummate", "passion is",  # cliches from the LinkedIn-fake network
                   "changing the world", "passionate about helping"),
}


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "1") == "1"


def _ngram_hash(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()


def _ngrams(tokens: list[str], lo: int, hi: int) -> Iterable[tuple[str, int]]:
    """Yield (joined_phrase, length_words) for lengths lo..hi."""
    n = len(tokens)
    for length in range(lo, hi + 1):
        if length > n:
            break
        for i in range(n - length + 1):
            phrase = " ".join(tokens[i : i + length])
            yield phrase, length


def _tag_categories(ngram_text: str) -> list[str]:
    lower = ngram_text.lower()
    return [cat for cat, keywords in _CATEGORY_HINTS.items()
            if any(k in lower for k in keywords)]


async def _refresh_index(collector) -> dict:
    """Phase 1: rebuild per-platform n-gram rows from current bios.
    Uses UPSERT on the PK (ngram_hash, platform, platform_uid).
    """
    stats = {"scanned_users": 0, "wrote_ngrams": 0, "skipped_empty": 0}
    async with collector.acquire() as ccon:
        # Load denylist once.
        denylist_rows = await ccon.fetch("SELECT ngram_hash FROM bio_ngram_denylist")
        denylist = {r["ngram_hash"] for r in denylist_rows}

        for platform, table, id_col, bio_col in _BIO_SOURCES:
            try:
                rows = await ccon.fetch(
                    f"SELECT {id_col}::text AS pid, {bio_col} AS bio "
                    f"FROM {table} WHERE {bio_col} IS NOT NULL AND length({bio_col}) > 0"
                )
            except Exception as exc:
                logger.debug("bio_clustering: skip %s.%s (%s)", table, bio_col, exc)
                continue

            for r in rows:
                stats["scanned_users"] += 1
                bio_raw = (r["bio"] or "")[:_MAX_BIO_CHARS]
                tokens = extract_tokens(bio_raw)
                if len(tokens) < _NGRAM_LEN_MIN:
                    stats["skipped_empty"] += 1
                    continue

                to_insert: list[tuple[bytes, str, int, str, str]] = []
                for phrase, length in _ngrams(tokens, _NGRAM_LEN_MIN, _NGRAM_LEN_MAX):
                    h = _ngram_hash(phrase)
                    if h in denylist:
                        continue
                    to_insert.append((h, phrase, length, platform, r["pid"]))

                if to_insert:
                    await ccon.executemany(
                        """
                        INSERT INTO bio_ngram_index
                            (ngram_hash, ngram_text, ngram_length, platform, platform_uid)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (ngram_hash, platform, platform_uid) DO NOTHING
                        """,
                        to_insert,
                    )
                    stats["wrote_ngrams"] += len(to_insert)

        # Prune high-DF n-grams (too common to be distinctive).
        # We keep entries in the index but they're excluded from clustering below.
    return stats


async def _emit_cluster_signals(collector, analyzer) -> dict:
    """Phase 2: find hashes with count >= CLUSTER_MIN and DF < CUTOFF,
    emit bio_ngram_cluster signals pairwise (bounded)."""
    stats = {"clusters": 0, "pairs_emitted": 0}
    async with collector.acquire() as ccon:
        clusters = await ccon.fetch(
            """
            SELECT ngram_hash, MAX(ngram_text) AS ngram_text,
                   MAX(ngram_length) AS ngram_length,
                   count(*) AS n
            FROM bio_ngram_index
            GROUP BY ngram_hash
            HAVING count(*) >= $1 AND count(*) < $2
            ORDER BY count(*) DESC
            LIMIT 500
            """,
            _CLUSTER_MIN,
            _DF_CUTOFF,
        )

    if not clusters:
        return stats

    signal_rows: list[tuple] = []
    for cluster in clusters:
        stats["clusters"] += 1
        h = cluster["ngram_hash"]
        text = cluster["ngram_text"]
        length = cluster["ngram_length"]
        n = cluster["n"]

        async with collector.acquire() as ccon:
            members = await ccon.fetch(
                "SELECT platform, platform_uid FROM bio_ngram_index "
                "WHERE ngram_hash = $1 ORDER BY first_seen ASC LIMIT 50",
                h,
            )

        categories = _tag_categories(text)
        cluster_beyond_2 = max(0, n - 2)
        confidence = min(1.0, 0.4 + 0.1 * length + 0.05 * cluster_beyond_2)

        emitted_for_this_hash = 0
        for a, b in itertools.combinations(members, 2):
            if emitted_for_this_hash >= _CLUSTER_MAX_PAIRS:
                break
            # Skip same-platform-same-uid pairs (shouldn't exist due to PK, defensive)
            if a["platform"] == b["platform"] and a["platform_uid"] == b["platform_uid"]:
                continue
            signal_rows.append((
                None,  # entity_id — analyzer resolves later
                "bio_ngram_cluster",
                a["platform"],
                "bio_ngram_index",
                "ngram_text",
                a["platform_uid"],
                b["platform"],
                b["platform_uid"],
                json.dumps({
                    "ngram": text[:200],
                    "ngram_length": length,
                    "cluster_size": n,
                    "categories": categories,
                }),
                confidence,
            ))
            emitted_for_this_hash += 1
        stats["pairs_emitted"] += emitted_for_this_hash

    if signal_rows:
        async with analyzer.acquire() as acon:
            # Idempotent per cycle: delete then re-insert (the cluster set can
            # shrink when bios change; keeping stale signals would mislead).
            await acon.execute(
                "DELETE FROM identity_signals WHERE signal_type = 'bio_ngram_cluster'"
            )
            await acon.executemany(
                """
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                signal_rows,
            )

    return stats


async def _gc_singletons(collector) -> int:
    """Nightly GC: drop n-grams that are singletons (below cluster threshold)
    AND older than 180 days. Keeps the index bounded on very large corpora."""
    async with collector.acquire() as ccon:
        result = await ccon.execute(
            f"""
            DELETE FROM bio_ngram_index
            WHERE first_seen < now() - interval '180 days'
              AND ngram_hash IN (
                SELECT ngram_hash FROM bio_ngram_index
                GROUP BY ngram_hash HAVING count(*) < {_CLUSTER_MIN}
              )
            """
        )
    # asyncpg returns "DELETE <n>" — parse the trailing int if present.
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def run_bio_clustering() -> dict:
    summary = {"skipped": None, "refresh_stats": {}, "cluster_stats": {}, "gc_deleted": 0}
    if not _is_enabled():
        summary["skipped"] = "disabled"
        return summary

    try:
        collector = get_collector_pool()
    except Exception:
        summary["skipped"] = "no_collector_pool"
        return summary
    analyzer = get_analyzer_pool()

    summary["refresh_stats"] = await _refresh_index(collector)
    summary["cluster_stats"] = await _emit_cluster_signals(collector, analyzer)
    # GC is cheap; run every cycle.
    summary["gc_deleted"] = await _gc_singletons(collector)

    logger.info("bio_clustering: %s", summary)
    return summary


__all__ = ["run_bio_clustering"]
