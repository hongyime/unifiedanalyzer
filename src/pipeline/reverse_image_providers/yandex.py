"""Yandex reverse image search adapter.

Preferred engine per Griffin's own methodology (2021-06-08 image
geolocation walkthrough). Yandex has no official public API; we
POST the image bytes to the image-upload endpoint and scrape the
JSON blob embedded in the response HTML.

Fragile. Rate-limits aggressively. Default enabled but with a very
low QPS. On 429 or shape drift, returns [] and lets the bridge's
circuit breaker take it offline for the rest of the day.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from . import ReverseImageHit, ReverseImageProvider

logger = logging.getLogger(__name__)

# Yandex embeds site data inside a <div class="Root"> or similar with
# a JSON payload keyed on "sites". Shape drifts; we only extract url +
# domain, which are the stable fields.
_SITES_RE = re.compile(r'"url"\s*:\s*"((?:[^"\\]|\\.)+?)"[^{}]*?"domain"\s*:\s*"((?:[^"\\]|\\.)+?)"')


def _decode(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s


class YandexProvider(ReverseImageProvider):
    NAME = "yandex"
    DEFAULT_QPS = 0.1  # 1 req / 10s
    DEFAULT_TIMEOUT = 15.0

    async def search(self, image_bytes: bytes) -> list[ReverseImageHit]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                # Step 1: upload to get an image ID / cbid.
                r = await client.post(
                    "https://yandex.com/images-apphost/image-download",
                    files={"upfile": ("image.jpg", image_bytes, "image/jpeg")},
                    headers=headers,
                    timeout=self.DEFAULT_TIMEOUT,
                )
                if r.status_code == 429:
                    logger.info("yandex reverse: 429")
                    return []
                if r.status_code >= 400:
                    logger.debug("yandex upload http %s", r.status_code)
                    return []
                try:
                    payload = r.json()
                except Exception:
                    return []
                cbid = payload.get("cbir_id") or payload.get("cbirId")
                if not cbid:
                    return []
                # Step 2: fetch results page.
                r2 = await client.get(
                    "https://yandex.com/images/search",
                    params={"rpt": "imageview", "cbir_id": cbid},
                    headers=headers,
                    timeout=self.DEFAULT_TIMEOUT,
                )
                if r2.status_code >= 400:
                    return []
                html = r2.text
        except Exception as exc:
            logger.debug("yandex reverse failed: %s", exc)
            return []

        hits: list[ReverseImageHit] = []
        for m in _SITES_RE.finditer(html):
            hits.append(ReverseImageHit(
                engine=self.NAME,
                url=_decode(m.group(1)),
                site_domain=_decode(m.group(2)),
            ))
            if len(hits) >= 20:
                break
        return hits


__all__ = ["YandexProvider"]
