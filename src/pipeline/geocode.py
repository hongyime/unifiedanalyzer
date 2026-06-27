"""Geocode Instagram place names -> coordinates for the map (IG stores
location_name but no lat/lng). Trickles via OSM Nominatim, cached in
geocode_cache. Rate-limited to honour Nominatim's usage policy (~1 req/s); slow
but free and one-time per distinct place.
"""
import asyncio
import logging

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)


def _geocode_one(place: str):
    import requests
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": "unifiedanalyzer/1.0 (personal OSINT)"},
            timeout=12,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        logger.debug("geocode failed for %r", place, exc_info=True)
    return None


async def geocode_step(batch: int = 30) -> dict:
    """Seed the cache with any new IG place names, then geocode up to `batch`
    pending ones. Call on a cadence from the scheduler."""
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    async with collector.acquire() as cc:
        names = await cc.fetch(
            "SELECT DISTINCT location_name FROM instagram_posts "
            "WHERE location_name IS NOT NULL AND location_name <> ''"
        )
    if names:
        async with analyzer.acquire() as conn:
            await conn.executemany(
                "INSERT INTO geocode_cache (place_name, status) VALUES ($1, 'pending') "
                "ON CONFLICT (place_name) DO NOTHING",
                [(n["location_name"],) for n in names],
            )

    async with analyzer.acquire() as conn:
        pending = await conn.fetch(
            "SELECT place_name FROM geocode_cache WHERE status = 'pending' LIMIT $1", batch
        )
    if not pending:
        return {"skipped": "nothing_pending"}

    done = ok = 0
    for p in pending:
        res = await asyncio.to_thread(_geocode_one, p["place_name"])
        async with analyzer.acquire() as conn:
            if res:
                await conn.execute(
                    "UPDATE geocode_cache SET lat=$1, lng=$2, status='ok', geocoded_at=NOW() WHERE place_name=$3",
                    res[0], res[1], p["place_name"],
                )
                ok += 1
            else:
                await conn.execute(
                    "UPDATE geocode_cache SET status='notfound', geocoded_at=NOW() WHERE place_name=$1",
                    p["place_name"],
                )
            done += 1
        await asyncio.sleep(1.1)  # Nominatim: <= 1 req/sec

    stats = {"geocoded": done, "resolved": ok}
    logger.info("Geocode step: %s", stats)
    return stats
