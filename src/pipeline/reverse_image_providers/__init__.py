"""Reverse-image-search provider adapters.

Do Next #5: multi-engine reverse image lookup. Griffin's "Solving an
Image Geolocation Challenge" (2021-06-08): "Never stop at one engine."

All 5 providers implement the same tiny ABC below. Each returns a
best-effort list of ReverseImageHit rows; failures are logged and
returned as an empty list plus a set error flag on the bridge summary.

Per-provider knobs (env):
  - <PROVIDER>_ENABLED (default 1 for yandex/tineye, 0 for the rest)
  - <PROVIDER>_QPS
  - <PROVIDER>_TIMEOUT_SECONDS
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ReverseImageHit:
    engine: str
    url: str
    thumbnail_url: Optional[str] = None
    title: Optional[str] = None
    site_domain: Optional[str] = None
    similarity: Optional[float] = None


class ReverseImageProvider:
    """ABC for reverse-image-search providers. Subclasses set NAME +
    override ``search(image_bytes)``."""

    NAME: str = ""
    DEFAULT_QPS: float = 0.2
    DEFAULT_TIMEOUT: float = 15.0

    async def search(self, image_bytes: bytes) -> list[ReverseImageHit]:
        raise NotImplementedError


__all__ = ["ReverseImageHit", "ReverseImageProvider"]
