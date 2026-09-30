"""TinEye reverse image search adapter.

TinEye has a public search page that accepts direct image uploads and
returns structured JSON. It's the cleanest and most reliable of the 5
providers; also has an official paid API for teams that need higher
volume (not used here).

Default enabled. Rate limit is polite (~0.5 QPS is safe).
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

import httpx

from . import ReverseImageHit, ReverseImageProvider

logger = logging.getLogger(__name__)


class TineyeProvider(ReverseImageProvider):
    NAME = "tineye"
    DEFAULT_QPS = 0.5
    DEFAULT_TIMEOUT = 20.0

    async def search(self, image_bytes: bytes) -> list[ReverseImageHit]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://tineye.com",
            "Referer": "https://tineye.com/",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                r = await client.post(
                    "https://tineye.com/api/v1/result_json/",
                    files={"image": ("image.jpg", image_bytes, "image/jpeg")},
                    headers=headers,
                    timeout=self.DEFAULT_TIMEOUT,
                )
                if r.status_code == 429:
                    logger.info("tineye reverse: 429")
                    return []
                if r.status_code >= 400:
                    logger.debug("tineye http %s", r.status_code)
                    return []
                try:
                    payload = r.json()
                except Exception:
                    return []
        except Exception as exc:
            logger.debug("tineye reverse failed: %s", exc)
            return []

        matches = payload.get("matches") or []
        hits: list[ReverseImageHit] = []
        for match in matches[:20]:
            backlinks = match.get("backlinks") or []
            for bl in backlinks[:3]:  # cap per match
                url = bl.get("backlink") or bl.get("url")
                if not url:
                    continue
                try:
                    domain = urlparse(url).hostname
                except Exception:
                    domain = None
                hits.append(ReverseImageHit(
                    engine=self.NAME,
                    url=url,
                    site_domain=domain,
                    thumbnail_url=match.get("image_url"),
                    title=bl.get("title"),
                    similarity=float(match.get("score", 0.0)) / 100.0 if match.get("score") else None,
                ))
        return hits


__all__ = ["TineyeProvider"]
