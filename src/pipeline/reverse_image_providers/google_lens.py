"""Google Lens reverse image search adapter.

Google Lens accepts direct image uploads at the ``lens.google.com``
domain but requires a session cookie to return meaningful results. Our
implementation stays cookie-free by fetching only the initial redirect
target that contains a JSON blob with ``visually_similar_images``.

Default DISABLED. Google is aggressive with unauthenticated scrapers;
operator opts in per case.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import httpx

from . import ReverseImageHit, ReverseImageProvider

logger = logging.getLogger(__name__)

_SIMILAR_RE = re.compile(
    r'"visually_similar_images"\s*:\s*\[(.*?)\]', re.DOTALL
)
_URL_RE = re.compile(r'"url"\s*:\s*"((?:[^"\\]|\\.)+?)"')


def _decode(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s


class GoogleLensProvider(ReverseImageProvider):
    NAME = "google_lens"
    DEFAULT_QPS = 0.1
    DEFAULT_TIMEOUT = 20.0

    async def search(self, image_bytes: bytes) -> list[ReverseImageHit]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                r = await client.post(
                    "https://lens.google.com/v3/upload",
                    files={"encoded_image": ("image.jpg", image_bytes, "image/jpeg")},
                    headers=headers,
                    timeout=self.DEFAULT_TIMEOUT,
                )
                if r.status_code == 429:
                    logger.info("google_lens reverse: 429")
                    return []
                if r.status_code >= 400:
                    logger.debug("google_lens http %s", r.status_code)
                    return []
                html = r.text
        except Exception as exc:
            logger.debug("google_lens reverse failed: %s", exc)
            return []

        section = _SIMILAR_RE.search(html)
        if not section:
            return []
        block = section.group(1)
        hits: list[ReverseImageHit] = []
        for m in _URL_RE.finditer(block):
            url = _decode(m.group(1))
            if not url.startswith("http"):
                continue
            try:
                domain = urlparse(url).hostname
            except Exception:
                domain = None
            hits.append(ReverseImageHit(engine=self.NAME, url=url, site_domain=domain))
            if len(hits) >= 20:
                break
        return hits


__all__ = ["GoogleLensProvider"]
