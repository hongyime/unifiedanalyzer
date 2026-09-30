"""SauceNAO reverse image search adapter.

SauceNAO has an official free API (100 searches/day, 4/30s). API key
loaded from env ``SAUCENAO_API_KEY``. Specialty: anime/art image
sources. Not the highest signal for a general OSINT workflow but
included because Griffin lists it.

Default DISABLED (requires API key).
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import httpx

from . import ReverseImageHit, ReverseImageProvider

logger = logging.getLogger(__name__)


class SauceNaoProvider(ReverseImageProvider):
    NAME = "saucenao"
    DEFAULT_QPS = 0.15
    DEFAULT_TIMEOUT = 20.0

    async def search(self, image_bytes: bytes) -> list[ReverseImageHit]:
        api_key = os.getenv("SAUCENAO_API_KEY")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://saucenao.com/search.php",
                    data={
                        "api_key": api_key,
                        "output_type": "2",  # JSON
                        "numres": "10",
                    },
                    files={"file": ("image.jpg", image_bytes, "image/jpeg")},
                    timeout=self.DEFAULT_TIMEOUT,
                )
                if r.status_code == 429:
                    logger.info("saucenao reverse: 429")
                    return []
                if r.status_code >= 400:
                    logger.debug("saucenao http %s", r.status_code)
                    return []
                try:
                    payload = r.json()
                except Exception:
                    return []
        except Exception as exc:
            logger.debug("saucenao reverse failed: %s", exc)
            return []

        results = payload.get("results") or []
        hits: list[ReverseImageHit] = []
        for res in results:
            hdr = res.get("header") or {}
            data = res.get("data") or {}
            similarity = None
            try:
                similarity = float(hdr.get("similarity", 0)) / 100.0
            except (TypeError, ValueError):
                pass
            for url in (data.get("ext_urls") or [])[:3]:
                try:
                    domain = urlparse(url).hostname
                except Exception:
                    domain = None
                hits.append(ReverseImageHit(
                    engine=self.NAME,
                    url=url,
                    site_domain=domain,
                    thumbnail_url=hdr.get("thumbnail"),
                    title=data.get("title") or data.get("source"),
                    similarity=similarity,
                ))
                if len(hits) >= 20:
                    break
            if len(hits) >= 20:
                break
        return hits


__all__ = ["SauceNaoProvider"]
