"""Bing Visual Search adapter.

Bing has a ``view=detailv2&iss=sbi`` flow that accepts a hosted-image
URL (or direct upload via the SBI form endpoint). We take the direct
upload path for consistency with the other providers. Bing embeds a
``#insightsToken`` JSON blob with similar-image entries.

Default DISABLED — operator opts in.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import httpx

from . import ReverseImageHit, ReverseImageProvider

logger = logging.getLogger(__name__)

_HREF_RE = re.compile(r'"purl"\s*:\s*"((?:[^"\\]|\\.)+?)"')


def _decode(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s


class BingProvider(ReverseImageProvider):
    NAME = "bing"
    DEFAULT_QPS = 0.2
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
                    "https://www.bing.com/images/search",
                    params={"view": "detailv2", "iss": "sbi"},
                    files={"imgurl": ("image.jpg", image_bytes, "image/jpeg")},
                    headers=headers,
                    timeout=self.DEFAULT_TIMEOUT,
                )
                if r.status_code == 429:
                    logger.info("bing reverse: 429")
                    return []
                if r.status_code >= 400:
                    logger.debug("bing http %s", r.status_code)
                    return []
                html = r.text
        except Exception as exc:
            logger.debug("bing reverse failed: %s", exc)
            return []

        hits: list[ReverseImageHit] = []
        for m in _HREF_RE.finditer(html):
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


__all__ = ["BingProvider"]
