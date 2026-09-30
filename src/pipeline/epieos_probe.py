"""Email -> Google gaia profile -> Maps reviews pivot (opt-in).

Griffin (@hatless1der)'s single-highest-leverage pivot from "Scam-a-Scammer"
(2023-05-18) and "Art of Pivoting" (2022-04-27): turn any email we already
have (github commits, breach exposure, WhatsApp bio, etc.) into a
geo-tagged Google Maps review cluster that reveals where the account owner
lives, works, and frequents.

**Default DISABLED** (EPIEOS_ENABLED=0). This capability is a real force
multiplier and sits closer to the ethics line than the Do Now items —
operator opens per case, not always-on.

Reads emails from identity_signals (signal_type IN ('commit_email',
'email_match'), value=email). Caches into collector.google_profile_cache
(the collector Postgres is the shared cache; analyzer reads from it).

Emits:
  - google_gaia_email_match (0.55) - two analyzer entities that share an
    email are already the same person; this codifies that link with the
    Google display_name + avatar as corroboration.
  - maps_review_geo_cluster (0.35) - two entities with reviews within
    500m of each other.

Ref: Z:\\...\\research\\spec-do-next-1-epieos-probe.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from typing import Optional

import httpx

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.google_profile_client import (
    GaiaProfile,
    GoogleProfileShapeError,
    MapsProfile,
    fetch_maps_contributions,
    lookup_gaia,
)

logger = logging.getLogger(__name__)

_ENABLED = "EPIEOS_ENABLED"
_BATCH = int(os.getenv("EPIEOS_BATCH", "5"))
_QPS = float(os.getenv("EPIEOS_QPS", "0.2"))     # 5s between requests
_STALE_DAYS = int(os.getenv("EPIEOS_STALE_DAYS", "60"))
_MAX_REVIEW_PAGES = int(os.getenv("EPIEOS_MAX_REVIEW_PAGES", "3"))
_TIMEOUT = float(os.getenv("EPIEOS_TIMEOUT_SECONDS", "15"))
_GEO_CLUSTER_METERS = float(os.getenv("EPIEOS_GEO_CLUSTER_METERS", "500"))


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "0") == "1"


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters. Standard formula."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _load_candidate_emails(collector, analyzer) -> list[str]:
    """Emails known to the analyzer that haven't been probed / are stale."""
    async with analyzer.acquire() as acon:
        rows = await acon.fetch(
            """
            SELECT DISTINCT lower(value) AS email
            FROM identity_signals
            WHERE signal_type IN ('commit_email', 'email_match')
              AND value ~* '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'
            """
        )
    if not rows:
        return []
    candidate_emails = [r["email"] for r in rows]

    async with collector.acquire() as ccon:
        cached_rows = await ccon.fetch(
            """
            SELECT email FROM google_profile_cache
            WHERE email = ANY($1::text[])
              AND (stale_after IS NULL OR stale_after > now())
            """,
            candidate_emails,
        )
    cached = {r["email"] for r in cached_rows}
    fresh = [e for e in candidate_emails if e not in cached]
    return fresh[:_BATCH]


async def _upsert_cache(
    collector, email: str, gaia: GaiaProfile, maps: Optional[MapsProfile]
) -> None:
    """Store the probe result. maps=None if gaia lookup failed."""
    reviews_json = None
    if maps and maps.reviews:
        reviews_json = json.dumps([
            {
                "place_name": rv.place_name,
                "place_id": rv.place_id,
                "rating": rv.rating,
                "text": rv.text,
                "posted_at": rv.posted_at,
                "lat": rv.lat,
                "lng": rv.lng,
            }
            for rv in maps.reviews
        ])

    async with collector.acquire() as ccon:
        await ccon.execute(
            """
            INSERT INTO google_profile_cache (
                email, gaia_id, display_name, avatar_url,
                has_profile, maps_reviews, maps_photos_urls,
                raw_payload, source, fetched_at, stale_after, last_error
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'gaia',
                      now(), now() + ($9::int || ' days')::interval, $10)
            ON CONFLICT (email) DO UPDATE SET
                gaia_id = EXCLUDED.gaia_id,
                display_name = EXCLUDED.display_name,
                avatar_url = EXCLUDED.avatar_url,
                has_profile = EXCLUDED.has_profile,
                maps_reviews = EXCLUDED.maps_reviews,
                maps_photos_urls = EXCLUDED.maps_photos_urls,
                raw_payload = EXCLUDED.raw_payload,
                fetched_at = now(),
                stale_after = EXCLUDED.stale_after,
                last_error = EXCLUDED.last_error
            """,
            email,
            gaia.gaia_id,
            gaia.display_name,
            gaia.avatar_url,
            gaia.has_profile,
            reviews_json,
            None,
            json.dumps(gaia.raw_payload) if gaia.raw_payload else None,
            _STALE_DAYS,
            gaia.error or (maps.error if maps else None),
        )


async def _emit_gaia_email_signals(analyzer, email: str, gaia: GaiaProfile) -> int:
    """When gaia lookup succeeded, link every entity that already had this
    email to the display_name / avatar. Confidence 0.9."""
    if not gaia.has_profile or not gaia.gaia_id:
        return 0
    async with analyzer.acquire() as acon:
        rows = await acon.fetch(
            """
            SELECT DISTINCT entity_id::text AS entity_id
            FROM identity_signals
            WHERE signal_type IN ('commit_email', 'email_match')
              AND lower(value) = $1
              AND entity_id IS NOT NULL
            LIMIT 20
            """,
            email,
        )
    if not rows:
        return 0
    signal_rows = [(
        r["entity_id"],
        "google_gaia_email_match",
        "google",
        "google_profile_cache",
        "gaia_id",
        gaia.gaia_id,
        "entity",
        r["entity_id"],
        json.dumps({
            "email": email,
            "gaia_id": gaia.gaia_id,
            "display_name": gaia.display_name,
        }),
        0.9,
    ) for r in rows]
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


async def _emit_review_cluster_signals(collector, analyzer, email: str, maps: MapsProfile) -> int:
    """For each entity that ALSO has an email in google_profile_cache, check
    whether >= 2 review-locations of THIS email land within GEO_CLUSTER_METERS
    of >= 2 review-locations of THEIR email. Emit maps_review_geo_cluster.
    v1: pairwise, bounded. Full-graph cluster analysis is a separate pass."""
    if not maps.reviews:
        return 0
    my_pts = [(rv.lat, rv.lng) for rv in maps.reviews if rv.lat is not None and rv.lng is not None]
    if len(my_pts) < 2:
        return 0

    # Find other cached emails with review clusters.
    async with collector.acquire() as ccon:
        others = await ccon.fetch(
            """
            SELECT email, maps_reviews
            FROM google_profile_cache
            WHERE email != $1
              AND has_profile = true
              AND maps_reviews IS NOT NULL
            LIMIT 200
            """,
            email,
        )
    if not others:
        return 0

    # Load the entities linked to THIS email once.
    async with analyzer.acquire() as acon:
        my_entities = await acon.fetch(
            """
            SELECT DISTINCT entity_id::text AS entity_id
            FROM identity_signals
            WHERE signal_type IN ('commit_email', 'email_match')
              AND lower(value) = $1
              AND entity_id IS NOT NULL
            """,
            email,
        )
    if not my_entities:
        return 0

    emitted = 0
    for row in others:
        their_email = row["email"]
        try:
            their_reviews = json.loads(row["maps_reviews"])
        except Exception:
            continue
        their_pts = [(rv.get("lat"), rv.get("lng")) for rv in their_reviews
                     if rv.get("lat") is not None and rv.get("lng") is not None]
        if len(their_pts) < 2:
            continue

        # Count pairs within GEO_CLUSTER_METERS.
        pairs = 0
        for a in my_pts:
            for b in their_pts:
                if _haversine_m(a[0], a[1], b[0], b[1]) <= _GEO_CLUSTER_METERS:
                    pairs += 1
                    if pairs >= 2:
                        break
            if pairs >= 2:
                break
        if pairs < 2:
            continue

        # Get their entities.
        async with analyzer.acquire() as acon:
            their_entities = await acon.fetch(
                """
                SELECT DISTINCT entity_id::text AS entity_id
                FROM identity_signals
                WHERE signal_type IN ('commit_email', 'email_match')
                  AND lower(value) = $1
                  AND entity_id IS NOT NULL
                """,
                their_email,
            )
        if not their_entities:
            continue

        # Confidence: cap at 0.5 * min(cluster_size) / 3 (per spec).
        confidence = min(0.5, 0.5 * min(len(my_pts), len(their_pts)) / 3.0)
        signal_rows = []
        for me in my_entities:
            for you in their_entities:
                if me["entity_id"] == you["entity_id"]:
                    continue
                signal_rows.append((
                    me["entity_id"],
                    "maps_review_geo_cluster",
                    "google",
                    "google_profile_cache",
                    "maps_reviews",
                    email,
                    "entity",
                    you["entity_id"],
                    json.dumps({"my_email": email, "their_email": their_email, "pairs": pairs}),
                    confidence,
                ))
        if signal_rows:
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
            emitted += len(signal_rows)
    return emitted


async def run_epieos_probe() -> dict:
    summary = {"skipped": None, "probed": 0, "with_profile": 0, "reviews_captured": 0,
               "signals_emitted": 0, "errors": 0}
    if not _is_enabled():
        summary["skipped"] = "disabled"
        return summary

    try:
        collector = get_collector_pool()
    except Exception:
        summary["skipped"] = "no_collector_pool"
        return summary
    analyzer = get_analyzer_pool()

    emails = await _load_candidate_emails(collector, analyzer)
    if not emails:
        return summary

    interval = 1.0 / max(_QPS, 0.01)
    async with httpx.AsyncClient() as client:
        for email in emails:
            summary["probed"] += 1
            try:
                gaia = await lookup_gaia(client, email, timeout=_TIMEOUT)
            except GoogleProfileShapeError as exc:
                logger.error("epieos_probe: SHAPE ERROR %s - disabling for this run", exc)
                summary["errors"] += 1
                break
            except Exception as exc:
                logger.warning("epieos_probe: gaia error for %s: %s", email, exc)
                summary["errors"] += 1
                await asyncio.sleep(interval)
                continue

            if gaia.error == "rate_limited":
                logger.warning("epieos_probe: rate-limited, bailing cycle")
                break

            maps: Optional[MapsProfile] = None
            if gaia.has_profile and gaia.gaia_id:
                summary["with_profile"] += 1
                try:
                    maps = await fetch_maps_contributions(
                        client, gaia.gaia_id, max_pages=_MAX_REVIEW_PAGES, timeout=_TIMEOUT
                    )
                    summary["reviews_captured"] += len(maps.reviews) if maps else 0
                except Exception as exc:
                    logger.warning("epieos_probe: maps error for %s: %s", email, exc)

            await _upsert_cache(collector, email, gaia, maps)

            summary["signals_emitted"] += await _emit_gaia_email_signals(analyzer, email, gaia)
            if maps and maps.reviews:
                summary["signals_emitted"] += await _emit_review_cluster_signals(
                    collector, analyzer, email, maps
                )

            await asyncio.sleep(interval)

    logger.info("epieos_probe: %s", summary)
    return summary


__all__ = ["run_epieos_probe"]
