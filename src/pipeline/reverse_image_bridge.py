"""Multi-engine reverse-image lookup bridge (Do Next #5).

For a bounded batch of media rows (typically avatars/portrait faces),
fan out to enabled providers in parallel, aggregate results into
``media_analysis`` rows of ``analysis_type = 'reverse_image_search'``,
and emit two identity-scorer signals:

- reverse_image_domain_match (0.30) - two entities' avatars produce
  hits on the same site+path combination. Strong "this face appears
  where the other person is named" signal.
- avatar_public_reappearance (0.25, context-only) - any hit above
  threshold. Just a "worth looking at" flag for the operator.

Per-provider circuit breaker: 5 consecutive failures disables that
provider for 6 hours, others keep running. Global kill switch:
REVERSE_IMAGE_ENABLED=0.

Ref: Z:\\...\\research\\spec-do-next-5-reverse-image-bridge.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from typing import Iterable, Optional

from src.db.connection import get_analyzer_pool
from src.pipeline.reverse_image_providers import ReverseImageHit, ReverseImageProvider
from src.pipeline.reverse_image_providers.yandex import YandexProvider
from src.pipeline.reverse_image_providers.tineye import TineyeProvider
from src.pipeline.reverse_image_providers.google_lens import GoogleLensProvider
from src.pipeline.reverse_image_providers.bing import BingProvider
from src.pipeline.reverse_image_providers.saucenao import SauceNaoProvider

logger = logging.getLogger(__name__)

_ENABLED = "REVERSE_IMAGE_ENABLED"
_BATCH = int(os.getenv("REVERSE_IMAGE_BATCH", "5"))
_STORE_THUMBS = os.getenv("REVERSE_IMAGE_STORE_THUMBNAILS", "0") == "1"
_MIN_HITS_TO_STORE = int(os.getenv("REVERSE_IMAGE_MIN_HITS_TO_STORE", "1"))
_STALE_HOURS = float(os.getenv("REVERSE_IMAGE_STALE_HOURS", "168"))  # 7 days

# Per-provider defaults (env override: <NAME>_ENABLED). Yandex + TinEye
# default on because Griffin's own methodology relies on Yandex first;
# the rest default off because they require careful rate management.
_DEFAULT_ENABLED = {
    "yandex": "1",
    "tineye": "1",
    "google_lens": "0",
    "bing": "0",
    "saucenao": "0",
}

# Circuit breaker: (fail_count, disabled_until_ts)
_BREAKER: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
_BREAKER_TRIP_AFTER = int(os.getenv("REVERSE_IMAGE_BREAKER_FAILS", "5"))
_BREAKER_DISABLE_SECONDS = int(os.getenv("REVERSE_IMAGE_BREAKER_SECONDS", str(6 * 3600)))


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "0") == "1"


def _provider_enabled(name: str) -> bool:
    return os.getenv(f"{name.upper()}_ENABLED", _DEFAULT_ENABLED.get(name, "0")) == "1"


def _breaker_open(name: str) -> bool:
    _, until = _BREAKER[name]
    return time.monotonic() < until


def _breaker_record(name: str, ok: bool) -> None:
    fails, until = _BREAKER[name]
    if ok:
        _BREAKER[name] = (0, 0.0)
        return
    fails += 1
    if fails >= _BREAKER_TRIP_AFTER:
        logger.warning(
            "reverse_image: %s tripped after %d fails - disabling for %ds",
            name, fails, _BREAKER_DISABLE_SECONDS,
        )
        _BREAKER[name] = (fails, time.monotonic() + _BREAKER_DISABLE_SECONDS)
    else:
        _BREAKER[name] = (fails, until)


def _all_providers() -> Iterable[ReverseImageProvider]:
    yield YandexProvider()
    yield TineyeProvider()
    yield GoogleLensProvider()
    yield BingProvider()
    yield SauceNaoProvider()


async def _fetch_next_batch(analyzer) -> list[dict]:
    """Pick media rows with faces that haven't had a recent reverse search."""
    async with analyzer.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ma.media_item_id
            FROM media_analysis ma
            WHERE ma.analysis_type = 'face_detection'
              AND ma.has_face = true
              AND NOT EXISTS (
                SELECT 1 FROM media_analysis ma2
                WHERE ma2.media_item_id = ma.media_item_id
                  AND ma2.analysis_type = 'reverse_image_search'
                  AND ma2.updated_at > now() - ($1::float || ' hours')::interval
              )
            ORDER BY ma.updated_at DESC
            LIMIT $2
            """,
            _STALE_HOURS,
            _BATCH,
        )
    return [dict(r) for r in rows]


async def _load_image_bytes(analyzer, media_item_id: str) -> Optional[bytes]:
    """Try to fetch bytes from media_analysis (if we stored them) or the
    associated media_items.file_path (bind-mounted at /media in workers)."""
    async with analyzer.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ma.crop_bytes, mi.file_path
            FROM media_analysis ma
            LEFT JOIN media_items mi ON mi.id = ma.media_item_id
            WHERE ma.media_item_id = $1 AND ma.analysis_type = 'face_detection'
            LIMIT 1
            """,
            media_item_id,
        )
    if not row:
        return None
    if row.get("crop_bytes"):
        return row["crop_bytes"]
    path = row.get("file_path")
    if path:
        try:
            from pathlib import Path
            return Path(path).read_bytes()
        except Exception:
            return None
    return None


async def _search_one_provider(provider: ReverseImageProvider, image_bytes: bytes) -> tuple[str, list[ReverseImageHit], bool]:
    """Wrap search() with circuit-breaker + timing. Returns (name, hits, ok)."""
    if _breaker_open(provider.NAME):
        return provider.NAME, [], False
    try:
        hits = await provider.search(image_bytes)
    except Exception as exc:
        logger.warning("reverse_image[%s]: search failed: %s", provider.NAME, exc)
        _breaker_record(provider.NAME, ok=False)
        return provider.NAME, [], False
    _breaker_record(provider.NAME, ok=True)
    return provider.NAME, hits, True


async def _store_aggregate(analyzer, media_item_id: str, aggregate: dict) -> None:
    async with analyzer.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO media_analysis (media_item_id, analysis_type, result_json, updated_at)
            VALUES ($1, 'reverse_image_search', $2::jsonb, now())
            ON CONFLICT (media_item_id, analysis_type) DO UPDATE
            SET result_json = EXCLUDED.result_json,
                updated_at = now()
            """,
            media_item_id,
            json.dumps(aggregate),
        )


async def _emit_signals(analyzer, media_item_id: str, hits: list[dict]) -> int:
    """For every OTHER entity whose reverse-image hits share a domain+path
    with ours, emit reverse_image_domain_match. For any entity with >=1
    hit above threshold, emit avatar_public_reappearance."""
    if not hits:
        return 0
    my_pairs = {(h.get("site_domain"), h.get("url")) for h in hits if h.get("site_domain")}
    if not my_pairs:
        return 0

    # my entity for this media_item_id
    async with analyzer.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT e.id::text AS entity_id
            FROM entities e
            JOIN entity_faces ef ON ef.entity_id = e.id
            JOIN media_analysis ma ON ma.media_item_id = $1
            WHERE ma.face_embedding IS NOT NULL
            LIMIT 1
            """,
            media_item_id,
        )
    my_entity = row["entity_id"] if row else None
    if not my_entity:
        return 0

    # Other cached reverse-search rows.
    async with analyzer.acquire() as conn:
        others = await conn.fetch(
            """
            SELECT ma.media_item_id, ma.result_json,
                   ef.entity_id::text AS entity_id
            FROM media_analysis ma
            JOIN entity_faces ef ON ef.media_item_id = ma.media_item_id
            WHERE ma.analysis_type = 'reverse_image_search'
              AND ma.media_item_id != $1
              AND ef.entity_id::text != $2
            LIMIT 200
            """,
            media_item_id,
            my_entity,
        )

    signal_rows: list[tuple] = []
    for row in others:
        try:
            data = json.loads(row["result_json"])
        except Exception:
            continue
        their_hits = data.get("hits") or []
        their_pairs = {(h.get("site_domain"), h.get("url")) for h in their_hits if h.get("site_domain")}
        overlap = my_pairs & their_pairs
        if not overlap:
            continue
        # confidence = 0.5 for domain match, higher if we caught the exact URL twice
        confidence = 0.5
        sig_row = (
            my_entity,
            "reverse_image_domain_match",
            "reverse_image",
            "media_analysis",
            "result_json",
            media_item_id,
            "entity",
            row["entity_id"],
            json.dumps({"overlap": list(overlap)[:5]}),
            confidence,
        )
        signal_rows.append(sig_row)

    # Emit an "avatar_public_reappearance" for the operator flag.
    signal_rows.append((
        my_entity,
        "avatar_public_reappearance",
        "reverse_image",
        "media_analysis",
        "result_json",
        media_item_id,
        "entity",
        my_entity,
        json.dumps({"unique_domains": list({p[0] for p in my_pairs})[:10]}),
        0.5,
    ))

    if signal_rows:
        async with analyzer.acquire() as conn:
            await conn.executemany(
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


async def run_reverse_image_bridge() -> dict:
    summary = {"skipped": None, "media_processed": 0, "providers_ok": 0,
               "providers_failed": 0, "hits": 0, "signals_emitted": 0}
    if not _is_enabled():
        summary["skipped"] = "disabled"
        return summary

    try:
        analyzer = get_analyzer_pool()
    except Exception:
        summary["skipped"] = "no_analyzer_pool"
        return summary

    active_providers = [p for p in _all_providers() if _provider_enabled(p.NAME) and not _breaker_open(p.NAME)]
    if not active_providers:
        summary["skipped"] = "no_active_providers"
        return summary

    rows = await _fetch_next_batch(analyzer)
    if not rows:
        return summary

    for row in rows:
        media_id = row["media_item_id"]
        image_bytes = await _load_image_bytes(analyzer, media_id)
        if image_bytes is None:
            continue
        summary["media_processed"] += 1

        results = await asyncio.gather(*(
            _search_one_provider(p, image_bytes) for p in active_providers
        ), return_exceptions=False)

        aggregated_hits: list[dict] = []
        providers_ok: list[str] = []
        providers_failed: list[str] = []
        for name, hits, ok in results:
            if ok:
                providers_ok.append(name)
                for h in hits:
                    d = {
                        "engine": h.engine,
                        "url": h.url,
                        "site_domain": h.site_domain,
                        "title": h.title,
                        "similarity": h.similarity,
                    }
                    if _STORE_THUMBS and h.thumbnail_url:
                        d["thumbnail_url"] = h.thumbnail_url
                    aggregated_hits.append(d)
            else:
                providers_failed.append(name)

        summary["providers_ok"] += len(providers_ok)
        summary["providers_failed"] += len(providers_failed)
        summary["hits"] += len(aggregated_hits)

        if len(aggregated_hits) < _MIN_HITS_TO_STORE and not providers_ok:
            # Skip write: pure-fail run. We'll try again next cycle.
            continue

        aggregate = {
            "queried_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "providers_ok": providers_ok,
            "providers_failed": providers_failed,
            "hits": aggregated_hits,
            "aggregate": {
                "unique_domains": sorted({h["site_domain"] for h in aggregated_hits if h.get("site_domain")}),
                "top_similarity": max([h["similarity"] for h in aggregated_hits if h.get("similarity") is not None] or [None]),
            },
        }
        await _store_aggregate(analyzer, media_id, aggregate)
        summary["signals_emitted"] += await _emit_signals(analyzer, media_id, aggregated_hits)

    logger.info("reverse_image_bridge: %s", summary)
    return summary


__all__ = ["run_reverse_image_bridge"]
